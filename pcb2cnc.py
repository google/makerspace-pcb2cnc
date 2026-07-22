#!/usr/bin/env python3
#
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import abc
import argparse
import contextlib
import dataclasses
import enum
import io
import math
import numbers
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import tomllib
import types
import typing
import warnings

from decimal import Decimal

from collections.abc import Callable, Iterator, Mapping
from typing import IO, overload, Self, Type


TOML_BARE_KEY = re.compile(r'^[A-Za-z0-9_-]+$')


def toml_key_escape(part: str) -> str:
  if TOML_BARE_KEY.fullmatch(part):
    return part
  else:
    return repr(part)


def toml_subkey(previous_parsed_from: str | None, subkey: str) -> str:
  if previous_parsed_from is None:
    return toml_key_escape(subkey)
  else:
    return f"{previous_parsed_from}.{toml_key_escape(subkey)}"


@dataclasses.dataclass(frozen=True)
class Tool:
  """A single CNC cutting tool.

  Attributes:
    number: The number of the tool, for use in M6 tool-change commands.
    diameter: The maximum cutting diameter of the tool, in mm.
    ball_diameter: The diameter of the rounded ball portion of the tool, in mm.
    angle: The included angle of the tool, in degrees, or 0.0 if the tool is
      not a V-cutting tool (i.e. it is an end or ball mill).
  """

  number: int
  diameter: Decimal
  ball_diameter: Decimal
  angle: Decimal

  def effective_diameter(self, cut_depth: Decimal) -> Decimal:
    r"""Computes the effective diameter of this tool at the provided cutting depth.

    The tool is expected to have a profile that looks like this:

    |       |
    |       | <--- Maximum diameter of the tool (self.diameter)
    \       /
     \     /
      \   / <----- V-shaped portion of the tool (included angle self.angle)
       \ /
        O <------- Ball end of the tool (diameter self.ball_diameter)

    End mills and ball mills are treated as special cases. End mills have an
    included angle of zero, and a ball diameter of zero. Ball mills have an
    included angle of zero, and a maximum diameter equal to their ball diameter.
    """
    if self.is_endmill:
      # Simple case: square end mill.
      return self.diameter

    ball_radius = self.ball_diameter / 2
    # One half of the included angle of the cutting tool, in radians. This
    # is the angle the outer surface of the V makes from the vertical.
    half_angle_rad = (float(self.angle) * math.pi / 180.0) / 2.0
    # The "transition" cutting depth at which we switch from the ball portion
    # of the tool to the V portion. This happens where the V is tangent to
    # the ball. The line at a right angle to this point therefore (a) goes
    # through the center of the ball, because it's tangent to the ball outer
    # surface, and (b) is at an angle of half_angle_rad to horizontal,
    # because it's at a right angle to the V surface.
    #
    # From there, draw the right triangle formed with a hypotenuse along
    # that right-angle line, the adjacent side along the chord cutting
    # across the ball at the transition depth, and the opposite side down
    # the centerline of the tool. This triangle has an angle of
    # half_angle_rad, from (b) above, and a hypotenuse length of
    # ball_radius. Our transition depth is the distance between the very
    # bottom of the ball, at ball_radius from the center of the ball, and
    # the point where the chord line intersects the centerline, which is
    # ball_radius*sin(half_angle_rad) from the center of the ball.
    #
    # Therefore, our transition depth is:
    # ball_radius - (ball_radius * sin(half_angle_rad))
    #
    # Or, when simplified:
    transition_depth = ball_radius * Decimal(1.0 - math.sin(half_angle_rad))

    if cut_depth <= transition_depth:
      # We're in the ball portion of the cutting tool. Our effective
      # diameter is the diameter of the chord across the ball.
      # No fancy trigonometry here, just good old Pythagoras:
      # r_eff^2 + (r_ball - depth)^2 = r_ball^2
      # r_eff = sqrt(r_ball^2 - (r_ball - depth)^2)
      #       = sqrt(r_ball^2 - (r_ball^2 - 2*r_ball*depth + depth^2))
      #       = sqrt(2*r_ball*depth - depth^2)
      # d_eff = 2*r_eff =
      d = ((2 * ball_radius * cut_depth) - (cut_depth ** 2)).sqrt() * 2
      return min(d, self.diameter)

    # We're in the V portion of the cutting tool. The effective radius at
    # this point can be broken into two parts: the part resulting from the
    # ball below the transition point, and the part resulting from the
    # V above that point.
    #
    # The part resulting from the ball is equal to the effective diameter of
    # the tool at the transition point.  From above, the radius of the tool
    # at transition depth is the length of the adjacent side of the right
    # triangle (ball_radius * cos(half_angle_rad)).
    transition_radius = 2 * float(ball_radius) * math.cos(half_angle_rad)

    # To find the part resulting from the V, draw another right triangle
    # with its hypotenuse along the edge of the tool, and adjacent side
    # vertical through the edge of the tool at transition depth. Since the
    # adjacent side is vertical, we know the angle formed is half_angle_rad.
    # The adjacent side starts at transition depth and runs up to our
    # cutting depth, and we want the length of the opposite side, which will
    # be the width added, from centerline, beyond the tangent point. That
    # opposite side has length (d_cut - d_transition) * tan(half_angle_rad).
    v_radius = float(cut_depth - transition_depth) * math.tan(half_angle_rad)
    return min(Decimal(2 * (transition_radius + v_radius)), self.diameter)

  @property
  def is_endmill(self) -> bool:
    return not self.ball_diameter and not self.angle

  @classmethod
  def endmill(cls, number: int, diameter: Decimal) -> Self:
    return cls(
        number=number,
        diameter=diameter,
        ball_diameter=Decimal(),
        angle=Decimal(),
    )

  @classmethod
  def from_toml(cls, number: int, d: object, parsed_from: str) -> Self:
    if isinstance(d, numbers.Number):
      return Tool.endmill(number, d)
    elif isinstance(d, dict):
      if "diameter" not in d and "ball_diameter" not in d:
        raise KeyError(f"Tool {number} has no specified diameter")

      ball_diameter = get_decimal(d, "ball_diameter", 0, parsed_from)
      # Allow specifying ball mills with only ball_diameter.
      diameter = get_decimal(d, "diameter", ball_diameter, parsed_from)
      angle = get_decimal(d, "angle", 0, parsed_from)

      return cls(
          number = number,
          diameter = diameter,
          ball_diameter = ball_diameter,
          angle = angle,
      )
    else:
      raise TypeError(f"Can't parse tool {number} of type {type(d)}")


def get_decimal(
    d: dict,
    key: str,
    default: numbers.Number,
    parsed_from: str,
) -> Decimal:
  if key in d:
    value = d[key]
  else:
    value = default

  if isinstance(value, Decimal):
    return value
  elif isinstance(value, numbers.Number):
    return Decimal(value)
  else:
    key_name = toml_subkey(parsed_from, key)
    raise TypeError(f"Key {key_name}: expected a number, got {type(value)} instead")


def dataclass_field_has_default(field: dataclasses.Field) -> bool:
  return (field.default is not dataclasses.MISSING
          or field.default_factory is not dataclasses.MISSING)


@overload
def strip_union_none[T](ty: Type[T | None]) -> Type[T]:
  ...


@overload
def strip_union_none[T](ty: Type[T]) -> Type[T]:
  ...


def strip_union_none(ty):
  if not isinstance(ty, types.UnionType):
    return ty

  args = typing.get_args(ty)
  if len(args) != 2:
    return ty

  (a, b) = args
  if a == type(None):
    return b
  elif b == type(None):
    return a
  else:
    return ty


def erase_type(ty: Type) -> Type:
  return typing.get_origin(ty) or ty


def is_assignable(
    value: object,
    field_type: Type,
) -> bool:
  origin = typing.get_origin(field_type)
  if origin == list:
    if not isinstance(value, list):
      return False
    args = typing.get_args(field_type)
    if not args:
      return True
    elem_type = args[0]
    return all(
        is_assignable(elem, elem_type)
        for elem in value
    )
  elif origin == dict:
    if not isinstance(value, dict):
      return False
    args = typing.get_args(field_type)
    if not args:
      return True
    (key_type, value_type) = args
    return all(
        is_assignable(k, key_type)
        and is_assignable(v, value_type)
        for (k, v) in value.items()
    )
  else:
    return isinstance(value, field_type)


_DECIMAL_ZERO = Decimal()


def parse_simple_dataclass[T](
    cls: Type[T],
    toml_dict: dict,
    parsed_from: str | None,
    overrides: dict[str, object] = {},
) -> T:
  kwargs = overrides.copy()
  for field in dataclasses.fields(cls):
    if field.name in overrides:
      continue

    toml_value = toml_dict.get(field.name, None)
    non_optional_type = erase_type(strip_union_none(field.type))
    subkey = toml_subkey(parsed_from, field.name)

    if is_assignable(toml_value, field.type):
      kwargs[field.name] = toml_value
    elif (isinstance(_DECIMAL_ZERO, non_optional_type)
          and isinstance(toml_value, numbers.Number)):
      # For keys that want a Decimal, but where we have an int, widen the int.
      kwargs[field.name] = Decimal(toml_value)
    elif (issubclass(non_optional_type, enum.Enum)
          and isinstance(toml_value, type(next(iter(field.type)).value))):
      # Try to parse an enum from the value given.
      try:
        kwargs[field.name] = non_optional_type(toml_value)
      except ValueError:
        valid_options = ", ".join(repr(e.value) for e in non_optional_type)
        raise ValueError(
            f"Key {subkey}: unexpected option '{toml_value}'; "
            + f"valid options are: {valid_options}"
        ) from None
    elif (issubclass(non_optional_type, TomlParsable)
          and toml_value is not None):
      kwargs[field.name] = (
          non_optional_type.from_toml_object(toml_value, subkey)
      )
    elif field.name not in toml_dict and dataclass_field_has_default(field):
      # This field has a default and we didn't specify a value, so leave it
      # out of kwargs and let it take the default value.
      continue
    else:
      if toml_value is None:
        error_msg = f"a value of type {field.type} is required"
      else:
        error_msg = (
            f"expected a value of type {field.type}, "
            f"but found {type(toml_value)} instead"
        )
      raise ValueError(f"Key {subkey}: {error_msg}")

  return cls(**kwargs)


def from_dict_or_none[T](
    parser: Callable[[dict, str], T],
    maybe_dict: object,
    parsed_from: str | None,
) -> T | None:
  if isinstance(maybe_dict, dict):
    return parser(maybe_dict, parsed_from)
  elif maybe_dict is None:
    return None
  else:
    raise TypeError(
        f"{parsed_from}: expected a dict, found {type(maybe_dict)} instead"
    )

class TomlParsable:
  @classmethod
  def from_toml_dict(
      cls,
      toml_dict: dict,
      parsed_from: str | None,
  ) -> Self:
    return parse_simple_dataclass(cls, toml_dict, parsed_from)

  @classmethod
  def from_toml_object(
      cls,
      toml_object: object,
      parsed_from: str | None,
  ) -> Self | None:
    return from_dict_or_none(cls.from_toml_dict, toml_object, parsed_from)


@dataclasses.dataclass(frozen=True)
class TemplateToolSettings(TomlParsable):
  """A template for CNC settings to use a tool.

  Attributes:
    rpm: The spindle speed, in revolutions per minute.
    plunge: The vertical feed rate, in millimeters per minute.
    debug_parsed_from: The TOML key from which this template was read,
      for debugging purposes.
  """

  rpm: int | None
  plunge: int | None
  debug_parsed_from: str | None = (
      dataclasses.field(default=None, compare=False, kw_only=True)
  )

  @classmethod
  def from_toml_dict(
      cls,
      toml_dict: dict,
      parsed_from: str | None,
  ) -> Self:
    overrides = {"debug_parsed_from": parsed_from}
    return parse_simple_dataclass(cls, toml_dict, parsed_from, overrides)


@dataclasses.dataclass(frozen=True)
class TemplateMillSettings(TemplateToolSettings):
  """A template for CNC settings to use a tool for a milling operation.

  Attributes:
    feed: The horizontal feed rate, in millimeters per minute.
    depth_per_pass: The maximum depth that can be removed per pass, in
      millimeters. If more than this depth needs to be removed, the tool will
      make multiple passes.
  """

  feed: int | None
  depth_per_pass: Decimal | None


class MillOperation(enum.Enum):
  ISOLATION = "isolation"
  MILLDRILL = "milldrill"
  ROUTING = "routing"


@dataclasses.dataclass(frozen=True)
class DefaultMillSettings(TomlParsable):

  defaults: TemplateMillSettings | None
  operation_defaults: dict[MillOperation, TemplateMillSettings]

  @classmethod
  def from_toml_dict(
      cls,
      toml_dict: dict,
      parsed_from: str | None,
      allow_booleans_as_operations: bool = False,
  ) -> Self:
    def maybe_parse_subdict(
        subdict: object,
        parsed_from: str | None,
        allow_booleans: bool,
    ) -> TemplateMillSettings | None:
      if isinstance(subdict, bool) and allow_booleans:
        return None
      else:
        return from_dict_or_none(
            TemplateMillSettings.from_toml_dict,
            subdict,
            parsed_from,
        )
    
    defaults = maybe_parse_subdict(toml_dict, parsed_from, False)
    operation_defaults = {}

    for operation in MillOperation:
      operation_settings = maybe_parse_subdict(
          subdict = toml_dict.get(operation.value, None),
          parsed_from = toml_subkey(parsed_from, operation.value),
          allow_booleans = allow_booleans_as_operations,
      )

      if operation_settings is not None:
        operation_defaults[operation] = operation_settings

    return cls(defaults = defaults, operation_defaults = operation_defaults)


@dataclasses.dataclass(frozen=True)
class TemplateDrillSettings(TemplateToolSettings):
  """A template for CNC settings to use a drill.

  Attributes:
    plunge_max_ratio: The maximum ratio of the plunge rate, in millimeters per
      minute, and the diameter of the drill bit, in millimeters. If the plunge
      rate would exceed this ratio for a given tool, the plunge rate will be
      decreased, to avoid breaking smaller drill bits.
  """

  plunge_max_ratio: int | None


@dataclasses.dataclass(frozen=True)
class ToolSettings(TemplateToolSettings):
  """CNC settings to use a specific tool for a milling or drilling operation.

  Attributes:
    tool: The tool to which these settings apply.
  """

  tool: Tool
  rpm: int
  plunge: int
  debug_parsed_from: None = (
      dataclasses.field(default=None, init=False, repr=False, compare=False)
  )

  @classmethod
  def merge_templates(
      cls,
      tool: Tool,
      *templates: TemplateToolSettings | None,
      settings_type: str,
  ) -> Self:
    if not templates:
      raise ValueError("Must specify at least one template")

    filtered_templates = [t for t in templates if t is not None]
    if not filtered_templates:
      raise ValueError(
          f"No {settings_type} settings found; "
          + "are you missing a defaults section in config.toml?"
      )

    kwargs = {"tool": tool}

    for field in dataclasses.fields(cls):
      if field.name == "tool" or field.type == None or not field.init:
        continue

      for tpl in filtered_templates:
        value = getattr(tpl, field.name)
        if value is not None:
          kwargs[field.name] = value
          break
      else:
        if not dataclass_field_has_default(field):
          checked_templates = ", ".join(
              t.debug_parsed_from or "<unknown>"
              for t in filtered_templates
          )

          msg = (
              f"Missing key '{field.name}' in {settings_type} settings "
              + f"for tool #{tool.number} (checked: {checked_templates})"
          )

          raise KeyError(msg)

    return cls(**kwargs)


@dataclasses.dataclass(frozen=True)
class MillSettings(ToolSettings, TemplateMillSettings):
  """CNC settings to use a mill for a specific milling operation."""
  feed: int
  depth_per_pass: Decimal


@dataclasses.dataclass(frozen=True)
class DrillSettings(ToolSettings, TemplateDrillSettings):
  """CNC settings to use a specific drill."""

  plunge_max_ratio: int | None = dataclasses.field(repr=False, compare=False)

  def __post_init__(self):
    if self.plunge_max_ratio:
      ratio_limit = round(self.tool.diameter * self.plunge_max_ratio)
      plunge = min(self.plunge, ratio_limit)
      object.__setattr__(self, "plunge", plunge)

    object.__setattr__(self, "plunge_max_ratio", None)


@dataclasses.dataclass(frozen=True)
class MillWithOperations:
  tool: Tool
  operations: dict[MillOperation, MillSettings]

  @classmethod
  def from_toml_object(
      cls,
      toml_object: object,
      tool_number: int,
      parsed_from: str,
      global_defaults: DefaultMillSettings,
  ) -> Self:

    tool = Tool.from_toml(tool_number, toml_object, parsed_from)

    if isinstance(toml_object, numbers.Number) or toml_object is None:
      per_tool_settings = DefaultMillSettings(None, {})
    else:
      per_tool_settings = from_dict_or_none(
          lambda d, pf: (
              DefaultMillSettings.from_toml_dict(
                  toml_dict = d,
                  parsed_from = pf,
                  allow_booleans_as_operations = True,
              )
          ),
          toml_object,
          parsed_from
      )

    def wants_operation(operation: MillOperation) -> bool | None:
      if not isinstance(toml_object, dict):
        return None
      operation_object = toml_object.get(operation.value, None)

      if operation_object is None:
        return None
      elif isinstance(operation_object, dict):
        # We've explicitly specified config for an operation, so we want it.
        return True
      elif isinstance(operation_object, bool):
        return operation_object
      else:
        return None

    wanted_operations = set()
    for operation in MillOperation:
      wants = wants_operation(operation)
      if wants is None:
        # Unless specified:
        # - Isolation can be done with any tool.
        # - Routing and milldrill must be done with an endmill.
        wants = (operation == MillOperation.ISOLATION or tool.is_endmill)
      if wants:
        wanted_operations.add(operation)

    per_operation_config = {
        op: MillSettings.merge_templates(
            tool,
            # Resolution order:
            # - Per-tool per-operation config
            per_tool_settings.operation_defaults.get(op),
            # - Per-tool default config
            per_tool_settings.defaults,
            # - Global per-operation config
            global_defaults.operation_defaults.get(op),
            # - Global default config
            global_defaults.defaults,
            settings_type = op.value,
        )
        for op in wanted_operations
    }

    return cls(tool = tool, operations = per_operation_config)


@dataclasses.dataclass(frozen=True)
class MillRack(TomlParsable):
  tools: dict[int, MillWithOperations]
  global_default_settings: DefaultMillSettings

  @classmethod
  def from_toml_dict(
      cls,
      toml_dict: dict,
      parsed_from: str,
  ) -> Self:
    
    DEFAULTS_KEY = "defaults"
    global_defaults = (
        from_dict_or_none(
            DefaultMillSettings.from_toml_dict,
            toml_dict.get(DEFAULTS_KEY),
            toml_subkey(parsed_from, DEFAULTS_KEY)
        )
        or DefaultMillSettings(None, {})
    )

    tools = {}

    for (key, value) in toml_dict.items():
      if not key.isdecimal():
        continue

      tool_number = int(key)
      tool_parsed_from = toml_subkey(parsed_from, key)

      
      tools[tool_number] = MillWithOperations.from_toml_object(
          value,
          tool_number,
          tool_parsed_from,
          global_defaults,
      )

    return cls(tools = tools, global_default_settings = global_defaults)

  def tools_for(self, operation: MillOperation) -> dict[int, MillSettings]:
    tools = {}
    for (toolno, opers) in sorted(self.tools.items()):
      settings = opers.operations.get(operation, None)
      if settings:
        tools[toolno] = settings

    return tools


@dataclasses.dataclass(frozen=True)
class DrillSequence(TemplateDrillSettings):
  start: Decimal
  end: Decimal
  step: Decimal
  first: int
  number_step: int = dataclasses.field(default=1, kw_only=True)


  def __post_init__(self):
    if self.end < self.start and not self.step.is_signed():
      raise ValueError(
          f"Can't count forward by {self.step} from {self.start} to {self.end}"
      )
    if self.end > self.start and self.step.is_signed():
      raise ValueError(
          f"Can't count backward by {self.step.copy_abs()} "
          f"from {self.start} to {self.end}"
      )

  def __iter__(self) -> Iterator[Tool]:
    curr_diameter = self.start
    curr_tool_number = self.first

    while curr_diameter < self.end:
      yield Tool.endmill(curr_tool_number, curr_diameter)
      curr_diameter += self.step
      curr_tool_number += self.number_step


@dataclasses.dataclass(frozen=True)
class DrillRack(TomlParsable):
  drills: dict[int, DrillSettings]
  global_default_settings: TemplateDrillSettings | None

  @classmethod
  def from_toml_dict(
      cls,
      toml_dict: dict,
      parsed_from: str,
  ) -> Self:
    DEFAULTS_KEY = "defaults"
    global_defaults = from_dict_or_none(
        TemplateDrillSettings.from_toml_dict,
        toml_dict.get(DEFAULTS_KEY),
        toml_subkey(parsed_from, DEFAULTS_KEY),
    )

    SEQUENCE_KEY = "sequence"
    sequence_dicts = toml_dict.get(SEQUENCE_KEY, [])
    sequence_parsed_from = toml_subkey(parsed_from, SEQUENCE_KEY)
    if isinstance(sequence_dicts, list):
      sequences = [
        DrillSequence.from_toml_object(
            toml_object = d,
            parsed_from = f"{sequence_parsed_from}[{i}]",
        )
        for (i, d) in enumerate(sequence_dicts)
      ]
    else:
      sequences = [
        DrillSequence.from_toml_object(
            toml_object = sequence_dicts,
            parsed_from = sequence_parsed_from,
        )
      ]
      warnings.warn(
          f"Config key '{sequence_parsed_from}' was a table when it should be "
          f"a list of tables; please specify that key in your config as "
          f"[[{sequence_parsed_from}]], with double square brackets"
      )


    # Start by filling in the drill map from the sequences.
    drills: dict[int, tuple[Tool, list[TemplateDrillSettings]]] = {
        tool.number: (tool, [sequence])
        for sequence in sequences
        if sequence
        for tool in sequence
    }

    for (key, value) in toml_dict.items():
      if not key.isdecimal():
        continue

      number = int(key)
      drill_parsed_from = toml_subkey(parsed_from, key)

      if number in drills:
        (tool, settings) = drills[number]
      else:
        (tool, settings) = (None, [])

      if value is False:
        # Special sentinel value: delete that drill if it exists.
        drills.pop(number, None)
        continue
      elif isinstance(value, numbers.Number):
        tool = Tool.endmill(number, Decimal(value))
      elif isinstance(value, dict):
        # Try parsing this as a tool struct, in case we want a new diameter.
        # If we can't, that's not an issue, _unless_ this is a drill we haven't 
        # seen before.
        try:
          tool = Tool.from_toml(number, value, drill_parsed_from)
        except KeyError:
          if tool is None:
            raise

        settings.insert(
            0,
            TemplateDrillSettings.from_toml_dict(value, drill_parsed_from),
        )
      else:
        raise TypeError(
            f"Key {drill_parsed_from}: expected a number, dict, or False, "
            f"found {type(value)} instead"
        )

      drills[number] = (tool, settings)

    # Now we have the complete list of drills. Resolve their settings.
    drill_settings = {
        number: DrillSettings.merge_templates(
            tool,
            *settings,
            global_defaults,
            settings_type = "drill",
        )
        for (number, (tool, settings)) in sorted(drills.items())
    }

    return cls(
        drills = drill_settings,
        global_default_settings = global_defaults,
    )


type Millproject = dict[str, str | list[str]]


class MillprojectComponent(abc.ABC):
  @abc.abstractmethod
  def to_millproject(self) -> Millproject:
    ...


def to_millproject(
    comp: MillprojectComponent | None
) -> Millproject:
  return comp.to_millproject() if comp is not None else {}


def _lcbool(v: bool) -> str:
  return "true" if v else "false"


@dataclasses.dataclass(frozen=True)
class IsolationConfig(TomlParsable):
  depth: Decimal
  width: Decimal | None = None
  offset: Decimal = Decimal('0.0')
  voronoi: bool = False
  voronoi_preserve_thermals: bool = True
  overlap: str | Decimal = '20%'
  trace_preamble: str | None = None
  trace_postamble: str | None = None
  tools: list[int] = dataclasses.field(default_factory = list)


class BoardSide(enum.Enum):
  AUTO = 'auto'
  FRONT = 'front'
  BACK = 'back'


@dataclasses.dataclass(frozen=True)
class MilldrillConfig(TomlParsable):
  minimum: Decimal
  tool: int | None = None
  depth: Decimal | None = None


@dataclasses.dataclass(frozen=True)
class DrillingConfig(TomlParsable):
  depth: Decimal
  side: BoardSide = BoardSide.AUTO
  milldrill: MilldrillConfig | None = None
  single_size: bool = False


@dataclasses.dataclass(frozen=True)
class RoutingBridgeConfig(TomlParsable, MillprojectComponent):
  count: int
  width: Decimal
  depth: Decimal

  def to_millproject(self) -> Millproject:
    return {
        "bridgesnum": f"{self.count}",
        "bridges": f"{self.width}mm",
        "zbridges": f"-{abs(self.depth)}mm",
    }


@dataclasses.dataclass(frozen=True)
class RoutingConfig(TomlParsable):
  depth: Decimal
  side: BoardSide = BoardSide.AUTO
  bridges: RoutingBridgeConfig | None = None
  fill_outline: bool = True
  tool: int | None = None


@dataclasses.dataclass(frozen=True)
class OptimizeConfig(TomlParsable, MillprojectComponent):
  tolerance: Decimal = Decimal('0.01')
  gcode_optimize: Decimal = Decimal('0.00254')
  eulerian_paths: bool = True
  tsp_2opt: bool = True
  pathfinding_steps_limit: int = 1
  g0_vertical_speed: Decimal = Decimal('1270.0')
  g0_horizontal_speed: Decimal = Decimal('2540.0')
  backtrack: Decimal = Decimal('0.0')

  def to_millproject(self) -> Millproject:
    return {
        "tolerance": f"{self.tolerance}",
        "optimise": f"{self.gcode_optimize}mm",
        "eulerian-paths": _lcbool(self.eulerian_paths),
        "vectorial": "true",
        "tsp-2opt": _lcbool(self.tsp_2opt),
        "path-finding-limit": f"{self.pathfinding_steps_limit}",
        "g0-vertical-speed": f"{self.g0_vertical_speed}mm/min",
        "g0-horizontal-speed": f"{self.g0_horizontal_speed}mm/min",
        "backtrack": f"{self.backtrack}mm/min",
    }


@dataclasses.dataclass(frozen=True)
class CommonConfig(TomlParsable):
  mills: MillRack
  drills: DrillRack

  metric: bool = True
  zmove: Decimal = Decimal('3.0')
  zchange: Decimal | None = None
  zero_start: bool = True
  mirror_offset: Decimal = Decimal('0.0')
  mirror_y: bool = False
  tiles_x: int = 1
  tiles_y: int = 1
  offset_x: Decimal = Decimal('0.0')
  offset_y: Decimal = Decimal('0.0')
  preamble: str | None = None
  postamble: str | None = None

  diameter_precision: int = 3

  isolation: IsolationConfig | None = None
  drilling: DrillingConfig | None = None
  routing: RoutingConfig | None = None
  optimize: OptimizeConfig | None = None


@dataclasses.dataclass(frozen=True)
class ResolvedOperation:
  depth: Decimal
  rpm: int
  plunge: int

  @staticmethod
  def _warn_skipped(operation: str, section: str | None = None) -> None:
    warnings.warn(
        f"Skipping {operation} because the [{section or operation}] section "
        f"is missing in config.toml"
    )

  @staticmethod
  def _get_tools_for(
      config: CommonConfig,
      operation: MillOperation,
  ) -> dict[int, MillSettings]:
    tool_map = config.mills.tools_for(operation)
    if len(tool_map) == 0:
      raise ValueError(
          f"Configuration calls for {operation.value}, but no tools have been "
          f"configured for it. Add at least one tool in [mills] with "
          f"'{operation.value}' configured. You may need a "
          f"[mills.defaults.{operation.value}] section in your config."
      )
    return tool_map

  @staticmethod
  def _resolve_tool(
      tool_map: dict[int, MillSettings],
      toolno: int,
      config_path: str,
      operation: MillOperation,
  ) -> MillSettings:
    if toolno not in tool_map:
      raise ValueError(
          f"Tool #{toolno}, specified in {config_path}, is not configured for "
          f"{operation.value}. Add '{operation.value}' to [mills.{toolno}], "
          f"or choose a different tool for that operation."
      )
    return tool_map[toolno]


@dataclasses.dataclass(frozen=True)
class ResolvedMillOperation(ResolvedOperation):
  feed: int
  depth_per_pass: Decimal


@dataclasses.dataclass(frozen=True)
class ResolvedIsolation(ResolvedMillOperation, MillprojectComponent):
  config: IsolationConfig
  tools_by_diameter: Mapping[Decimal, MillSettings]

  @classmethod
  def resolve(cls, config: CommonConfig) -> Self | None:
    iso = config.isolation
    if iso is None:
      cls._warn_skipped("isolation")
      return None

    if iso.width is None and not iso.voronoi:
      raise ValueError(
          "Must specify isolation.width when isolation.voronoi is false"
      )
    
    tool_map = cls._get_tools_for(config, MillOperation.ISOLATION)
    depth = abs(iso.depth)

    if iso.tools:
      resolved_tool_settings = [
          cls._resolve_tool(
              tool_map,
              tool,
              "isolation.tools",
              MillOperation.ISOLATION,
          )
          for tool in iso.tools
      ]
    else:
      # automatic selection: use all available configured tools,
      # sorted in descending order of effective diameter
      resolved_tool_settings = sorted(
          tool_map.values(),
          key = lambda ms: ms.tool.effective_diameter(depth),
          reverse = True,
      )

    resolved_tools_by_diameter = {
        round(ms.tool.effective_diameter(depth), config.diameter_precision): ms
        for ms in resolved_tool_settings
    }

    # Use the safest feed and speed settings across all tools.
    # For tools that should go faster, we'll fix in post-processing.
    
    # To allow distinguishing feed and plunge rates, we decrease the plunge rate
    # by a tiny amount (1 mm/min) if the feed and plunge rates are the same.
    feed = min(ts.feed for ts in resolved_tool_settings)
    plunge = min(ts.plunge for ts in resolved_tool_settings)
    if feed == plunge:
      plunge -= 1

    return cls(
        config = iso,
        depth = depth,
        tools_by_diameter = types.MappingProxyType(resolved_tools_by_diameter),
        rpm = min(ts.rpm for ts in resolved_tool_settings),
        plunge = plunge,
        feed = feed,
        depth_per_pass = (
            min(ts.depth_per_pass for ts in resolved_tool_settings)
        ),
    )

  def to_millproject(self) -> Millproject:
    mill_diameters = ",".join(
        f"{diameter}mm"
        for diameter in self.tools_by_diameter.keys()
    )

    def split_or_empty(s: str | None) -> list[str]:
      if s:
        return [line for line in s.split("\n") if line]
      else:
        return []

    return {
        "zwork": f"-{self.depth}mm",
        "mill-feed": f"{self.feed}mm/min",
        "mill-speed": f"{self.rpm}rpm",
        "voronoi": _lcbool(self.config.voronoi),
        "preserve-thermal-reliefs": (
            _lcbool(self.config.voronoi_preserve_thermals)
        ),
        "offset": f"{self.config.offset}mm",
        "isolation-width": f"{self.config.width or 0}mm",
        "mill-diameters": mill_diameters,
        "milling-overlap": str(self.config.overlap),
        "pre-milling-gcode": split_or_empty(self.config.trace_preamble),
        "post-milling-gcode": split_or_empty(self.config.trace_postamble),
        "mill-vertfeed": f"{self.plunge}mm/min",
        "mill-infeed": f"{self.depth_per_pass}mm",
        "mill-feed-direction": "any",
        "invert-gerbers": "false",
        "draw-gerber-lines": "false",
    }


@dataclasses.dataclass(frozen=True)
class ResolvedMilldrill(ResolvedMillOperation, MillprojectComponent):
  config: MilldrillConfig
  tool_settings: MillSettings
  diameter: Decimal

  @classmethod
  def resolve(
      cls,
      config: CommonConfig,
      md: MilldrillConfig,
      drill_depth: Decimal,
  ) -> Self:
    if md.depth is None:
      depth = drill_depth
    else:
      depth = abs(md.depth)

    tool_map = cls._get_tools_for(config, MillOperation.MILLDRILL)
    if md.tool is not None:
      tool_settings = cls._resolve_tool(
          tool_map,
          md.tool,
          "drilling.milldrill.tool",
          MillOperation.MILLDRILL,
      )
    else:
      # automatic selection: the largest configured milldrill cutter
      # that's not larger than the milldrill size
      filtered_tools = [
          ts for ts in tool_map.values()
          if ts.tool.effective_diameter(depth) <= md.minimum
      ]
      if len(filtered_tools) == 0:
        smallest_tool_diameter = min(
            ts.tool.effective_diameter(depth)
            for ts in tool_map.values()
        )
        raise ValueError(
            f"All of your configured milldrill cutters are larger than the "
            f"configured milldrill minimum size of {md.minimum}. Increase "
            f"your milldrill size to at least {smallest_tool_diameter} mm, "
            f"or configure a smaller cutting tool with a 'milldrill' config."
        )
      tool_settings = max(
          filtered_tools,
          key = lambda ts: ts.tool.effective_diameter(depth)
      )

    return cls(
        config = md,
        depth = depth,
        tool_settings = tool_settings,
        diameter = round(
            tool_settings.tool.effective_diameter(depth),
            config.diameter_precision
        ),
        rpm = tool_settings.rpm,
        plunge = tool_settings.plunge,
        feed = tool_settings.feed,
        depth_per_pass = Decimal('+inf'),
    )

  def to_millproject(self) -> Millproject:
    return {
        "min-milldrill-hole-diameter": f"{self.config.minimum}mm",
        "zmilldrill": f"-{self.depth}mm",
        "milldrill-diameter": f"{self.diameter}mm",
    }


@dataclasses.dataclass(frozen=True)
class ResolvedDrilling(ResolvedOperation, MillprojectComponent):
  config: DrillingConfig
  milldrill: ResolvedMilldrill | None
  tools_by_diameter: Mapping[Decimal, DrillSettings]

  @classmethod
  def resolve(cls, config: CommonConfig) -> Self | None:
    drill = config.drilling
    if drill is None:
      cls._warn_skipped("drilling")
      return None

    drill_map = config.drills.drills
    depth = abs(drill.depth)
    if drill.milldrill:
      milldrill_config = ResolvedMilldrill.resolve(
          config,
          drill.milldrill,
          depth,
      )
      milldrill_size = drill.milldrill.minimum
    else:
      milldrill_config = None
      milldrill_size = Decimal('+inf')
    
    drills_by_diameter = {
        ds.tool.effective_diameter(depth): ds
        for ds in drill_map.values()
    }

    filtered_drills = {
        round(diameter, config.diameter_precision): settings
        for (diameter, settings) in sorted(drills_by_diameter.items())
        if diameter < milldrill_size
    }

    return cls(
        config = drill,
        depth = depth,
        rpm = min(ts.rpm for ts in filtered_drills.values()),
        plunge = min(ts.plunge for ts in filtered_drills.values()),
        milldrill = milldrill_config,
        tools_by_diameter = types.MappingProxyType(filtered_drills),
    )

  def to_millproject(self) -> Millproject:
    available_drills = ",".join(
        f"{diameter}mm"
        for diameter in self.tools_by_diameter.keys()
    )
    return {
        "zdrill": f"-{self.depth}mm",
        "drill-feed": f"{self.plunge}mm/min",
        "drill-speed": f"{self.rpm}rpm",
        "drill-side": self.config.side.value,
        "onedrill": _lcbool(self.config.single_size),
        "drills-available": available_drills,
        "nom6": "false",
        "nog81": "true",
        "nog91-1": "true",
        **to_millproject(self.milldrill),
    }



@dataclasses.dataclass(frozen=True)
class ResolvedRouting(ResolvedMillOperation, MillprojectComponent):
  config: RoutingConfig
  tool_settings: MillSettings
  diameter: Decimal

  @classmethod
  def resolve(
      cls,
      config: CommonConfig,
      milldrill: ResolvedMilldrill | None,
  ) -> Self | None:
    routing = config.routing
    if routing is None:
      cls._warn_skipped("routing")
      return None

    tool_map = cls._get_tools_for(config, MillOperation.ROUTING)
    depth = abs(routing.depth)
    if routing.tool is not None:
      tool_settings = cls._resolve_tool(
          tool_map,
          routing.tool,
          "routing.tool",
          MillOperation.ROUTING,
      )
    elif (milldrill is not None
          and milldrill.tool_settings.tool.number in tool_map):
      # Automatic selection part 1: use the milldrill tool if it exists
      # and can be used for routing, since that saves a tool change.
      tool_settings = tool_map[milldrill.tool_settings.tool.number]
    else:
      # Automatic selection part 2: use the smallest configured tool, since
      # it will be able to cut shapes most precisely.
      tool_settings = min(
          tool_map.values(),
          key = lambda ts: ts.tool.effective_diameter(depth),
      )

    return cls(
        config = routing,
        depth = depth,
        tool_settings = tool_settings,
        diameter = round(
            tool_settings.tool.effective_diameter(depth),
            config.diameter_precision,
        ),
        rpm = tool_settings.rpm,
        plunge = tool_settings.plunge,
        feed = tool_settings.feed,
        depth_per_pass = tool_settings.depth_per_pass,
    )

  def to_millproject(self) -> Millproject:
    return {
        "cutter-diameter": f"{self.diameter}mm",
        "zcut": f"-{self.depth}mm",
        "cut-feed": f"{self.feed}mm/min",
        "cut-vertfeed": f"{self.plunge}mm/min",
        "cut-speed": f"{self.rpm}rpm",
        "cut-infeed": f"{self.depth_per_pass}mm",
        "cut-side": self.config.side.value,
        "fill-outline": _lcbool(self.config.fill_outline),
        **to_millproject(self.config.bridges),
    }


@dataclasses.dataclass(frozen=True)
class ResolvedConfig(MillprojectComponent):
  config: CommonConfig
  isolation: ResolvedIsolation | None
  drilling: ResolvedDrilling | None
  milldrill: ResolvedMilldrill | None
  routing: ResolvedRouting | None

  @classmethod
  def resolve(cls, config: CommonConfig) -> Self:
    isolation = ResolvedIsolation.resolve(config)
    drilling = ResolvedDrilling.resolve(config)
    milldrill = drilling.milldrill if drilling else None
    routing = ResolvedRouting.resolve(config, milldrill)
    return cls(
        config = config,
        isolation = isolation,
        drilling = drilling,
        milldrill = milldrill,
        routing = routing,
    )

  def to_millproject(self) -> Millproject:
    return {
        "metric": _lcbool(self.config.metric),
        "metricoutput": "true",
        "zsafe": f"{self.config.zmove}mm",
        "zchange": f"{self.config.zchange or self.config.zmove}mm",
        "nog64": "true",
        "zero-start": _lcbool(self.config.zero_start),
        "mirror-axis": f"{self.config.mirror_offset}mm",
        "mirror-yaxis": _lcbool(self.config.mirror_y),
        "tile-x": f"{self.config.tiles_x}",
        "tile-y": f"{self.config.tiles_y}",
        "x-offset": f"{self.config.offset_x}mm",
        "y-offset": f"{self.config.offset_y}mm",
        "spinup-time": "0s",
        "spindown-time": "0s",

        **to_millproject(self.isolation),
        **to_millproject(self.drilling),
        **to_millproject(self.routing),
        **to_millproject(self.config.optimize),
    }


def load_config_from_dict(d: dict[str, object]) -> ResolvedConfig:
  config = CommonConfig.from_toml_object(d, None)
  return ResolvedConfig.resolve(config)


_CONFIG_FILE_NAME = "pcb2cnc.toml"


def find_config_file(
    explicit_config: pathlib.Path | None,
    input_dir: pathlib.Path | None,
) -> pathlib.Path:
  if explicit_config:
    if explicit_config.is_file():
      return explicit_config
    else:
      raise FileNotFoundError(
          f"Configuration file {explicit_config} was explicitly specified, "
          f"but could not be found"
      )

  if input_dir:
    cwd_config = input_dir / _CONFIG_FILE_NAME
    if cwd_config.is_file():
      return cwd_config

  xdg_config_dir = os.environ.get("XDG_CONFIG_HOME") or os.environ.get("XDG_CONFIG_DIR")
  if xdg_config_dir:
    config_base = pathlib.Path(xdg_config_dir)
  else:
    config_base = pathlib.Path.home() / ".config"
  home_config = config_base / _CONFIG_FILE_NAME
  if home_config.is_file():
    return home_config

  default_config = pathlib.Path(__file__).resolve().parent / _CONFIG_FILE_NAME
  if default_config.is_file():
    return default_config

  raise FileNotFoundError("Could not find a configuration file!")


def load_config(
    explicit_config: pathlib.Path | None = None,
    input_dir: pathlib.Path | None = None,
) -> ResolvedConfig:
  return load_config_from_file(find_config_file(explicit_config, input_dir))


def load_config_from_file(filename: os.PathLike) -> ResolvedConfig:
  with open(filename, "rb") as fh:
    toml_dict = tomllib.load(fh, parse_float = Decimal)

  return load_config_from_dict(toml_dict)


def load_config_from_string(config: str) -> ResolvedConfig:
  toml_dict = tomllib.loads(config, parse_float = Decimal)
  return load_config_from_dict(toml_dict)


def write_millproject(millproject: Millproject, dest: IO[str]):
  for (k, v) in millproject.items():
    if isinstance(v, list):
      dest.writelines(
          f"{k} = {line}\n"
          for line in v
          if line
      )
    else:
      dest.write(f"{k} = {v}\n")


###
###
###


class CopperSide(enum.Enum):
  FRONT = enum.auto()
  BACK = enum.auto()
  FRONT_AND_BACK = enum.auto()

  @property
  def front(self) -> bool:
    return self == CopperSide.FRONT or self == CopperSide.FRONT_AND_BACK

  @property
  def back(self) -> bool:
    return self == CopperSide.BACK or self == CopperSide.FRONT_AND_BACK


@dataclasses.dataclass(frozen=True)
class GerberFileSet:
  front: os.PathLike | None = None
  back: os.PathLike | None = None
  outline: os.PathLike | None = None
  drill: os.PathLike | None = None

  @classmethod
  def load(
      cls,
      directory: os.PathLike,
      copper: CopperSide,
  ) -> Self:
    path = pathlib.Path(directory)

    def single_file_or_fail(*suffixes: str, enable=True) -> os.PathLike | None:
      if not enable:
        return None

      matched_files = [
          file
          for suffix in suffixes
          for file in path.glob(f"*{suffix}", case_sensitive=False)
          if file.is_file()
      ]

      if len(matched_files) > 1:
        raise ValueError(
            f"Found multiple files in {path} matching "
            f"*{{{",".join(suffixes)}}}: "
            f"[{", ".join(map(os.fspath, matched_files))}]"
        )
      elif matched_files:
        return matched_files[0]
      else:
        return None

    return cls(
        front = single_file_or_fail("-F_Cu.gbr", ".gtl", enable=copper.front),
        back = single_file_or_fail("-B_Cu.gbr", ".gbl", enable=copper.back),
        outline = single_file_or_fail("-Edge_Cuts.gbr", ".gm1"),
        drill = single_file_or_fail(".drl"),
    )


GCODE_COMMENT = re.compile(r"\s*[(][^)]*[)]\s*$")
GCODE_SKIP_LINES = re.compile(r"^(T[0-9]+|M0|M2|M9)\s.*$")
GCODE_TOOL_CHANGE_MESSAGE = re.compile(
    r"^\(MSG, Change tool bit to ([a-z]+) .* ([0-9.]+)\s*mm\)"
)
GCODE_TOOL_CHANGE_COMMAND = re.compile(r"^M0*6\s.*")
GCODE_FEED_COMMAND = re.compile(r"F([0-9.]+)")


class BasePostprocessor(abc.ABC):
  current_tool_config: ToolSettings | None

  def __init__(self):
    self.current_tool_config = None

  @abc.abstractmethod
  def identify_tool(self, diameter: Decimal) -> ToolSettings:
    raise KeyError(f"Unknown tool with diameter {diameter}")

  def replace_feed(self, feed: Decimal) -> Decimal | None:
    return None

  def process_line(self, line: str) -> list[str]:
    if not line or line.isspace():
      return []
    elif GCODE_SKIP_LINES.match(line):
      return []
    elif match := GCODE_TOOL_CHANGE_MESSAGE.match(line):
      diameter = Decimal(match.group(2))
      self.current_tool_config = self.identify_tool(diameter)
      return []
    elif GCODE_TOOL_CHANGE_COMMAND.match(line):
      cfg = self.current_tool_config
      return [
          f"(MSG, Change tool: #{cfg.tool.number})\n"
          f"M6 T{self.current_tool_config.tool.number}\n",
          f"G0 S{self.current_tool_config.rpm}\n",
      ]
    elif GCODE_COMMENT.fullmatch(line):
      # The entire line is a comment - skip it
      return []
    else:
      line = GCODE_COMMENT.sub("\n", line)
      if match := GCODE_FEED_COMMAND.search(line):
        parsed_feed = Decimal(match.group(1))
        new_feed = self.replace_feed(parsed_feed)
        if new_feed is not None:
          line = line[:match.start(1)] + str(new_feed) + line[match.end(1):]
      return [line]

  def postprocess(self, input: IO[str], output: IO[str]):
    for line_in in input:
      output.writelines(self.process_line(line_in))


class SingleToolPostprocessor(BasePostprocessor):
  tool_diameter: Decimal
  tool_config: ToolSettings

  def __init__(self, tool_diameter: Decimal, tool_config: ToolSettings):
    super().__init__()
    self.tool_diameter = tool_diameter
    self.tool_config = tool_config
  
  def identify_tool(self, diameter: Decimal) -> ToolSettings:
    if diameter == self.tool_diameter:
      return self.tool_config
    return super().identify_tool(diameter)


class IsolationPostprocessor(BasePostprocessor):
  config: ResolvedIsolation

  def __init__(self, config: ResolvedIsolation):
    super().__init__()
    self.config = config

  def identify_tool(self, diameter: Decimal) -> MillSettings:
    if diameter in self.config.tools_by_diameter:
      return self.config.tools_by_diameter[diameter]
    return super().identify_tool(diameter)

  def replace_feed(self, feed: Decimal) -> Decimal | None:
    if not self.current_tool_config:
      return None
    if feed == self.config.plunge:
      return Decimal(self.current_tool_config.plunge)
    if feed == self.config.feed:
      return Decimal(self.current_tool_config.feed)
    return None


class DrillingPostprocessor(BasePostprocessor):
  config: ResolvedDrilling

  def __init__(self, config: ResolvedDrilling):
    super().__init__()
    self.config = config

  def identify_tool(self, diameter: Decimal) -> DrillSettings:
    if diameter in self.config.tools_by_diameter:
      return self.config.tools_by_diameter[diameter]
    return super().identify_tool(diameter)

  def replace_feed(self, feed: Decimal) -> Decimal | None:
    if not self.current_tool_config:
      return None
    if feed == self.config.plunge:
      return Decimal(self.current_tool_config.plunge)
    return None


@dataclasses.dataclass(frozen=True)
class Pcb2gcodeProcessor:
  config: ResolvedConfig
  copper_side: CopperSide
  inputs: GerberFileSet | None
  working_dir: pathlib.Path

  def run_pcb2gcode(self):
    with io.StringIO() as iobuf:
      write_millproject(self.config.to_millproject(), iobuf)
      millproject = iobuf.getvalue()

    input_file_args = []
    inputs = self.inputs
    if not inputs:
      raise ValueError("Cannot run pcb2gcode on no inputs")

    if inputs.front:
      input_file_args.extend(["--front", os.fspath(inputs.front)])
    if inputs.back:
      input_file_args.extend(["--back", os.fspath(inputs.back)])
    if inputs.outline:
      input_file_args.extend(["--outline", os.fspath(inputs.outline)])
    if inputs.drill:
      input_file_args.extend(["--drill", os.fspath(inputs.drill)])

    if not input_file_args:
      raise ValueError("No input files found - nothing to do")

    self.working_dir.mkdir(parents = True, exist_ok = True)

    subprocess.run(
        [
            "pcb2gcode",
            "--config", "/proc/self/fd/0",
            "--output-dir", os.fspath(self.working_dir),
            *input_file_args,
        ],
        input = millproject,
        capture_output = False,
        stdout = sys.stdout,
        stderr = sys.stderr,
        check = True,
        text = True,
        encoding = "utf-8",
    )

  def postprocess(
      self,
      output_front: IO[str] | None = None,
      output_back: IO[str] | None = None,
  ):
    front_files = []
    back_files = []

    if self.config.isolation:
      if self.copper_side.front:
        front_files.append(
            ("front.ngc", IsolationPostprocessor(self.config.isolation))
        )
      if self.copper_side.back:
        back_files.append(
            ("back.ngc", IsolationPostprocessor(self.config.isolation))
        )

    def files_for_side(side: BoardSide):
      if side == BoardSide.FRONT:
        return front_files
      elif side == BoardSide.BACK:
        return back_files
      elif self.copper_side == CopperSide.BACK:
        return back_files
      else:
        return front_files

    if self.config.drilling:
      files_for_side(self.config.drilling.config.side).append(
          ("drill.ngc", DrillingPostprocessor(self.config.drilling))
      )

    if self.config.milldrill:
      files_for_side(self.config.drilling.config.side).append(
          (
              "milldrill.ngc",
              SingleToolPostprocessor(
                  self.config.milldrill.diameter,
                  self.config.milldrill.tool_settings,
              )
          )
      )

    if self.config.routing:
      files_for_side(self.config.routing.config.side).append(
          (
              "outline.ngc",
              SingleToolPostprocessor(
                  self.config.routing.diameter,
                  self.config.routing.tool_settings,
              )
          )
      )

    def filter_exists(file_list):
      return [
          (file, postproc)
          for (file, postproc) in file_list
          if (self.working_dir / file).exists()
      ]
    
    front_files = filter_exists(front_files)
    back_files = filter_exists(back_files)

    if front_files and not output_front:
      raise ValueError(
          f"Front-side output file must be specified, because these files "
          f"want to be machined from the front: "
          f"[{", ".join(file for (file, _) in front_files)}]"
      )

    if back_files and not output_back:
      raise ValueError(
          f"Back-side output file must be specified, because these files "
          f"want to be machined from the back: "
          f"[{", ".join(file for (file, _) in back_files)}]"
      )

    def postprocess_all(files, output):
      if not output:
        return
      if self.config.config.preamble:
        output.write(self.config.config.preamble)
      for (file, postproc) in files:
        path = self.working_dir / file
        if path.exists():
          output.write(f"(=== file: {file} ===)\n")
          with open(path, "rt") as input_file:
            postproc.postprocess(input_file, output)
      if self.config.config.postamble:
        output.write(self.config.config.postamble)

    postprocess_all(front_files, output_front)
    postprocess_all(back_files, output_back)


def main():
  parser = argparse.ArgumentParser(description="Convert PCB gerbers to Carbide 3D compatible gcode.")
  parser.add_argument("--config", type=str, help="Path to config file")

  subparsers = parser.add_subparsers(dest="command", required=True)

  # millproject
  parser_millproject = subparsers.add_parser("millproject", help="Write the millproject generated by the current config to stdout.")

  # postprocess
  parser_postprocess = subparsers.add_parser("postprocess", help="Postprocess existing gcode.")
  parser_postprocess.add_argument("directory", type=str, help="Directory with gcode")
  parser_postprocess.add_argument("--output-front", type=str, help="Output front gcode file")
  parser_postprocess.add_argument("--output-back", type=str, help="Output back gcode file")
  parser_postprocess.add_argument("--copper-side", type=str, choices=["front", "back", "both"], default="back", help="Copper side to process")

  # run
  parser_run = subparsers.add_parser("run", help="Run pcb2gcode over Gerbers, and postprocess the results.")
  parser_run.add_argument("directory", type=str, help="Directory with gerbers")
  parser_run.add_argument("--output-front", type=str, help="Output front gcode file")
  parser_run.add_argument("--output-back", type=str, help="Output back gcode file")
  parser_run.add_argument("--copper-side", type=str, choices=["front", "back", "both"], default="back", help="Copper side to process")

  args = parser.parse_args()

  def path_or_none(path: str | None) -> pathlib.Path | None:
    return pathlib.Path(path) if path else None

  if args.command == "millproject":
    config = load_config(explicit_config = path_or_none(args.config))
    write_millproject(config.to_millproject(), sys.stdout)
    return
  
  config = load_config(
      explicit_config = path_or_none(args.config),
      input_dir = pathlib.Path(args.directory),
  )

  side_map = {
      "front": CopperSide.FRONT,
      "back": CopperSide.BACK,
      "both": CopperSide.FRONT_AND_BACK,
  }
  copper_side = side_map[args.copper_side]

  if args.command in ("postprocess", "run"):
    with contextlib.ExitStack() as stack:
      if args.command == "run":
        inputs = GerberFileSet.load(args.directory, copper_side)
        tempdir = stack.enter_context(tempfile.TemporaryDirectory())
        working_dir = pathlib.Path(tempdir)
      else:
        inputs = None
        working_dir = pathlib.Path(args.directory)

      processor = Pcb2gcodeProcessor(
          config = config,
          copper_side = copper_side,
          inputs = inputs,
          working_dir = working_dir,
      )

      if args.command == "run":
        processor.run_pcb2gcode()

      out_front = (
          stack.enter_context(open(args.output_front, "wt"))
          if args.output_front else None
      )
      out_back = (
          stack.enter_context(open(args.output_back, "wt"))
          if args.output_back else None
      )

      processor.postprocess(output_front=out_front, output_back=out_back)

if __name__ == "__main__":
  main()
