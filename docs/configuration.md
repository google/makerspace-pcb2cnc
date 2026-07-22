# `pcb2cnc.toml`

The `pcb2cnc.toml` file contains information about your available milling tools, configuration options for `pcb2gcode`, and general program configuration. The defaults shipped with `pcb2cnc` are designed for use with [Carbide3D Nomad](https://carbide3d.cam/nomad/) desktop CNC mills, and specify feeds, speeds, and geometries for the endmills, engravers, and drill bits available from Carbide3D that are most useful for milling circuit boards - the #112 1/16" endmill, the #501 and #502 V-bit engravers, and drill bits from 0.3mm to 1.2mm (assigned numbers 903-912). To change these settings, add or remove tools, or use a different machine, you will need to create a `pcb2cnc.toml` manually.

## Configuring tool geometry

You will need to provide `pcb2cnc` with information about the geometry of the cutters, drill bits, and engraver bits you have available for use on your machine.  `pcb2cnc` supports end mills (flat-ended), ball mills, and V-bits, including V-bits with a rounded tip.

### Configuring mills and engravers

Mills and engravers are specified in the `[mills]` section of `pcb2cnc.toml`.  Each tool has an associated tool number, which is used in the `T` argument to the `M6` tool-change command when that tool is loaded. Some tool ecosystems, such as Carbide3D's, assign specific meaning to individual tool numbers, but `pcb2cnc` allows you to select any number for a tool, so long as it is unique in your configuration.

#### Configuring endmills

To configure an endmill, specify the `diameter` for the tool in millimeters:

```toml
[mills.112]
diameter = 1.590
```

As a shortcut, you can also specify the diameter of an endmill directly in the `[mills]` section, without needing to open a new TOML section:

```toml
[mills]
112 = 1.590
```

#### Configuring ball mills

To configure a ball mill, specify the `ball_diameter` for the tool in millimeters:

```toml
[mills.111]
ball_diameter = 1.590
```

#### Configuring V-bits

To configure a V-bit, specify the included angle of the tool in degrees, and the maximum cutting diameter in millimeters:

```toml
[mills.302]
angle = 60
diameter = 12.70
```

For V-bits with a rounded tip, specify the corner diameter of the tip as its `ball_diameter`:

```toml
[mills.501]
angle = 60
diameter = 3.17
ball_diameter = 0.250
```

### Configuring drill bits

Drill bits are specified in the `[drills]` section of `pcb2cnc.toml`. As with mills, drills have an associated number used for tool changes. While it is possible to configure a mill and a drill with the same number, and `pcb2cnc` will use the correct tool based on the operation, this can lead to confusion during tool changes and is not recommended.

To specify a drill, specify the `diameter` of the bit in millimeters, using the same syntax as for mills:

```toml
[drills.901]
diameter = 0.1
```

As with mills, you can specify the diameter directly under the `[drills]` section as a shortcut:

```toml
[drills]
901 = 0.1
```

While drills can also have a `ball_diameter` or `angle` specified, attempting to perform drilling using a non-endmill tool geometry will lead to holes becoming too wide at the top, and is not recommended.

#### Drill bit sequences

PCB drill bits are often sold as packs containing a range of sizes. Rather than needing to specify each bit size in the range individually, `pcb2cnc` supports a syntax for configuring a sequence of drill bits all at once:

```toml
[[drills.sequence]]
start = 0.1
end = 3.0
step = 0.1
first = 901
```

This example creates a series of drill bits from 0.1mm to 3.0mm inclusive, in 0.1mm increments. The smallest bit, the 0.1mm bit, is assigned number 901, and each subsequent bit is assigned the next number after that - the 0.2mm bit will have number 902, the 0.3mm bit #903, and so forth, all the way through the 3.0mm bit at number 930. If sequential bit numbers are not suitable for that bit series, you can specify `number_step` to increase the amount each bit's number is incremented.

If you have multiple series of drill bits, you can specify the `[[drills.sequence]]` section multiple times:

```toml
[[drills.sequence]]
start = 0.1
end = 1.0
step = 0.1
first = 901

[[drills.sequence]]
start = 2.0
end = 3.0
step = 0.2
first = 920
number_step = 2
```

This configures bits with sizes 0.1mm (901), 0.2mm (902), and so on to 1.0mm (910); and 2.0mm (920), 2.2mm (922), and so on to 3.0mm (930).

If your sequence is missing one or more bits (for example, because the bit is broken), you can remove them from the sequence by setting their number to `false` in `[drills]`:

```toml
[[drills.sequence]]
start = 0.1
end = 0.5
step = 0.1
first = 901

[drills]
903 = false

# configured drills: 901, 902, 904, 905
```

Bits that are explicitly configured under `[drills]` or `[drills.NNN]` will always take priority over drills from a sequence:

```toml
[[drills.sequence]]
start = 0.1
end = 0.5
step = 0.1
first = 901

[drills]
902 = 2.0

[drills.904]
diameter = 4.0

# 901 = 0.1mm, 902 = 2.0mm, 903 = 0.3mm, 904 = 4.0mm, 905 = 0.5mm
```

## Configuring milling operations

Once you have the tool geometry configured, you then need to decide on parameters for the three main milling operations needed to create a PCB: isolation, drilling, and routing. Each operation is controlled by its own section in the config file.

If you don't need to perform a given operation, the corresponding section can be omitted, including any required options. For example, if your board has no holes that need to be drilled, you can omit the `[drilling]` section entirely. However, if you specify _any_ options in a given section, all required options in that section must be provided.

### Isolation

The _isolation_ pass is the first pass made on the board. It removes the copper from the board in areas where your PCB design does not contain copper.

The isolation pass is designed to electrically separate nets from each other, and depending on the settings you use, the resulting copper layout on the board may look different from your design software. For the closest possible board appearance to your original design, set `voronoi` to `false`, and set `width` to a number larger than the size of your board. Note, however, that doing so may make the milling take a very long time, especially if you have large non-copper areas in relation to the size of your milling tools.  Milling time can be decreased by setting the `voronoi` option to `true`, by decreasing the `width` option, by configuring larger mills, or by adding copper pour areas, such as ground or power planes, to your PCB design.


#### Isolation options

```toml
[isolation]
depth = 0.1
width = 2.0
offset = 0.1
voronoi = false
voronoi_preserve_thermals = true
overlap = '20%'
trace_preamble = "M7"
trace_postamble = "M9"
tools = [112, 501]
```

##### `depth` _(decimal (mm), required)_ <a id="cfg.isolation.depth"></a>

The depth of the isolation cuts. This needs to be at _least_ the thickness of the copper layer on the copper-clad stock you are using, and should preferably be a bit deeper, to handle irregularities in zeroing or slightly out-of-flat PCB stock. Something in the 0.05-0.1mm range tends to work well here.

Corresponds to the `zwork` option of `pcb2gcode`.

##### `width` _(decimal (mm), required\*)_ <a id="cfg.isolation.width"></a>

The maximum width that will be milled away between zones. If you have large keep-out areas that need to be completely devoid of copper, consider increasing this to a large value. Note that this is the _maximum_ separation - if there is not enough space between zones, `pcb2gcode` will use all of the space it has available instead.

This is required when `voronoi` is `false`. Corresponds to the `isolation-width` 
option of `pcb2gcode`.

##### `offset` _(decimal (mm), default 0.0mm)_ <a id="cfg.isolation.offset"></a>

The amount by which the milling line will be outset from the trace. In effect, this makes all traces larger by that amount in both directions. This offset will be shrunk if necessary to allow traces to be properly isolated with the smallest configured tool. Use this if your milling machine or bits have slop/tolerance issues and can't be precisely positioned.

Corresponds to the `offset` option of `pcb2gcode`.

##### `voronoi` _(boolean, default false)_ <a id="cfg.isolation.voronoi"></a>

If set, all copper areas on the board will be expanded as much as possible, such that they are only separated by the minimum possible width (the diameter of the smallest configured isolation tool). This may make your board _look_ very strange, but the resulting G-code program will execute much more quickly, and the result will be _electrically_ identical. However, this may affect deliberate keep-out areas on your board, such as for antennas, or thermal reliefs around pads.

Corresponds to the `voronoi` option of `pcb2gcode`.

##### `voronoi_preserve_thermals` _(boolean, default true)_ <a id="cfg.isolation.voronoi_preserve_thermals"></a>

If set, the `voronoi` option will ignore any thermal relief gaps around pads. This option defines "thermal relief gaps" as any copper-free area completely surrounded on all sides by the same copper net. This does a fairly good job of picking up thermal reliefs in clear areas, but may fail to detect thermal reliefs near other pads or traces.

Corresponds to the `preserve-thermal-reliefs` option of `pcb2gcode`.

##### `overlap` _(decimal (mm) or string, default "20%")_ <a id="cfg.isolation.overlap"></a>

The amount by which adjacent toolpaths are overlapped when removing an area larger than the tool. Can be specified as a percent of the tool's diameter, or as a fixed dimension in millimeters.

Corresponds to the `milling-overlap` option of `pcb2gcode`.

##### `trace_preamble` _(string, optional)_ <a id="cfg.isolation.trace_preamble"></a>

A snippet of G-code that will be inserted before each trace is milled. Can be used to turn on air assist, coolant pumps, and so forth. If you have multiple instructions, you can use a TOML multi-line string (triple-quoted).

Corresponds to the `pre-milling-gcode` option of `pcb2gcode`.

##### `trace_postamble` _(string, optional)_ <a id="cfg.isolation.trace_postamble"></a>

A snippet of G-code that will be inserted after each trace is milled. This should undo the effects of `trace_preamble`. As with `trace_preamble`, if you need multiple instructions, use a TOML multi-line string.

Corresponds to the `post-milling-gcode` option of `pcb2gcode`.

##### `tools` _(list of integers (tool numbers), optional)_ <a id="cfg.isolation.tools"></a>

Overrides the [automatic tool selection logic](#tool-selection), and explicitly specifies the numbers of the tools to use for isolation. The tools will be used in the order they are specified in the list. For best results, specify tools in decreasing size order.

### Drilling and milldrilling

The _drilling_ pass is the second pass made on the board, and will drill holes for vias, through-hole components, and mounting holes. Each hole will be drilled using the drill bit with the diameter closest to the required hole size.

In addition, rather than needing to stock bits for every possible hole size, larger holes can be drilled via _milldrilling_, which routes out the hole using a routing tool, in much the same way as is done for the [outline of the board](#routing). This is slower per-hole than using a designated drill bit, but allows creating holes of any size with a single cutting tool.

#### Drilling options

```toml
[drilling]
depth = 1.8
side = "auto"
single_size = false
```

##### `depth` _(decimal (mm), required)_ <a id="cfg.drilling.depth"></a>

The depth below program zero to which the drill bit will be plunged. This should be slightly, but not significantly, higher than the thickness of your PCB stock.  A value too low will result in incomplete holes, while a value too high will result in holes being punched in the spoilboard, or whatever else is underneath your working area. For most common copper-clad FR1 stock, a value of 1.7 - 1.8 works well here.

Corresponds to the `zdrill` option of `pcb2gcode`.

##### `side` _(string: "front", "back", or "auto", default "auto")_ <a id="cfg.drilling.side"></a>

For double-sided board projects, controls which side will be facing up when the holes are drilled. Defaults to letting `pcb2gcode` decide - the chosen side will be printed in the output of `pcb2gcode`. Generally, `pcb2gcode` will choose the front side, unless _only_ the back is being isolated.

Corresponds to the `drill-side` option of `pcb2gcode`.

##### `single_size` _(boolean, default false)_ <a id="cfg.drilling.single_size"></a>

Tells `pcb2gcode` to pick one drill bit size to use to drill _all_ holes on the board. If your holes are all close in size, but slightly different, this may save tool-change time. This works reasonably well with through-hole components that tend to use a narrow range of hole sizes.

Corresponds to the `onedrill` option of `pcb2gcode`.

#### Milldrilling options

```toml
[drilling.milldrill]
minimum = 2.0
depth = 1.6
tool = 112
```

##### `minimum` _(decimal (mm), required)_ <a id="cfg.drilling.milldrill.minimum"></a>

The minimum diameter of a hole for it to be milldrilled. Holes below this size will be drilled with conventional drill bits.

Corresponds to the `min-milldrill-hole-diameter` option of `pcb2gcode`.

##### `depth` _(decimal (mm), default `drilling.depth`)_ <a id="cfg.drilling.milldrill.depth"></a>

The depth below program zero to which the mill will be plunged when milldrilling. If not provided, uses the depth from the main `[drilling]` section. See the [documentation for `drilling.depth`](#cfg.drilling.depth) for discussion on how to choose a depth.

Corresponds to the `zmilldrill` option of `pcb2gcode`.

##### `tool` _(integer (tool number), optional)_ <a id="cfg.drilling.milldrill.tool"></a>

Overrides the [automatic tool selection logic](#tool-selection) and explicitly chooses a tool number to use for milldrilling.

### Routing

The final pass, _routing_, cuts the board into its final size and shape. A mill is run along the perimeter of the board, carving away the entire thickness of the board material.

Generally, the routing will be done along the complete perimeter of the board, cutting it entirely free of the surrounding stock. However, if your workholding setup only holds the board at the edges or at defined points, you can add _bridges_ to leave the board connected to the surrounding stock via thin, easily-breakable tabs.

#### Routing options

```toml
[routing]
depth = 1.7
side = "auto"
fill_outline = true
tool = 112
```

##### `depth` _(decimal (mm), required)_ <a id="cfg.routing.depth"></a>

The depth below program zero at which the mill will be run along the board outline. This will likely be the same depth as your [drilling depth](#cfg.drilling.depth), but can be adjusted if needed.

Corresponds to the `zcut` option of `pcb2gcode`.

##### `side` _(string: "front", "back", or "auto", default "auto")_ <a id="cfg.routing.side"></a>

For double-sided board projects, controls which side will be facing up when the outline is routed. Behaves identically to the [`drilling.side` option](#cfg.drilling.side), but controlling the routing pass instead of the drilling pass.

Corresponds to the `cut-side` option of `pcb2gcode`.

##### `fill_outline` _(boolean, default true)_ <a id="cfg.routing.fill_outline"></a>

Allows the outline of the board to be specified as a chain of lines enclosing a shape, rather than a single enclosed polygon. KiCad users should leave this turned on, since KiCad's `Edge.Cuts` layer behaves this way.

Corresponds to the `fill-outline` option of `pcb2gcode`.

##### `tool` _(integer (tool number), optional)_ <a id="cfg.routing.tool"></a>

Overrides the [automatic tool selection logic](#tool-selection) and explicitly chooses a tool number to use 
for routing.

#### Routing bridge options

```toml
[routing.bridges]
count = 4
width = 10
depth = 1.0
```

##### `count` _(integer, required)_ <a id="cfg.routing.bridges.count"></a>

The number of bridges to create to connect the board to its surroundings.

Corresponds to the `bridgesnum` option of `pcb2gcode`.

##### `width` _(decimal (mm), required)_ <a id="cfg.routing.bridges.width"></a>

The width of the created bridge tab, in the direction parallel to the edge of the board.

Corresponds to the `bridges` option of `pcb2gcode`.

##### `depth` _(decimal (mm), required)_ <a id="cfg.routing.bridges.depth"></a>

The depth below program zero at which the bridges will be routed. If you want your bridges to be full-height, set this to a negative number.

Corresponds to the `zbridges` option of `pcb2gcode`.

## Feeds and speeds

When doing any machining, including PCB milling, it is crucial to run your tools at the correct spindle speed, horizontal movement rate, and vertical plunge rate. Collectively, these parameters are known as "feeds and speeds", and will be different depending on the tool in use and the material being cut. You will need to configure feeds and speeds for each tool and milling operation you would like to perform.

### Mill feeds and speeds

Feeds and speeds for mills can be set in four places, with different levels of specificity. More-specific settings override less specific ones. In order from most specific to least, they are:

* `[mills.NNN.<operation>]` controls the feeds and speeds for a given operation, using a given tool. For example, settings under `[mills.112.routing]` apply to outline routing using mill #112.
* `[mills.NNN]` controls the feeds and speeds for _any_ operation using a given tool.
* `[mills.defaults.<operation>]` controls the default feeds and speeds for that operation, using _any_ tool.
* `[mills.defaults]` is a fallback used if nothing more specific matches.

There are four feed and speed parameters for a mill:

* `rpm` _(integer, rev/min):_ The rate at which the spindle spins, measured in revolutions per minute.
* `feed` _(integer, mm/min):_ The rate at which the tool moves through the material horizontally, measured in millimeters per minute.
* `plunge` _(integer, mm/min):_ The rate at which the tool is advanced into the material vertically, when starting a cut from above the board.
* `depth_per_pass` _(decimal, mm):_ The maximum depth of material that can be removed in a single pass. If your board is thicker than this, the tool will make multiple passes, dropping lower each time.

#### Example mill feeds and speeds

```toml
[mills.defaults]
rpm = 10000
depth_per_pass = 6.0

[mills.defaults.isolation]
feed = 400
plunge = 300

[mills.defaults.routing]
feed = 200
plunge = 200

[mills.112]
diameter = 1.590
feed = 300

[mills.112.milldrill]
plunge = 200

[mills.122]
diameter = 0.789
rpm = 15000
```

| Operation | Tool | Feed | From                       | Plunge | From                       | RPM   | From             |
|-----------|------|------|----------------------------|--------|----------------------------|-------|------------------|
| Isolation | 112  | 300  | `mills.112`                | 300    | `mills.defaults.isolation` | 10000 | `mills.defaults` |
| Isolation | 122  | 400  | `mills.defaults.isolation` | 300    | `mills.defaults.isolation` | 15000 | `mills.122`      |
| Routing   | 112  | 300  | `mills.112`                | 200    | `mills.defaults.routing`   | 10000 | `mills.defaults` |
| Routing   | 122  | 200  | `mills.defaults.routing`   | 200    | `mills.defaults.routing`   | 15000 | `mills.122`      |
| Milldrill | 112  | 300  | `mills.112`                | 200    | `mills.112.milldrill`      | 10000 | `mills.defaults` |
| Milldrill | 122  | *    | (error: not found)         | *      | (error: not found)         | 15000 | `mills.122`      |
         
Because tool #122 in this example is missing settings for milldrill, attempting to use it for milldrilling will cause an error.

### Drill feeds and speeds

Feeds and speeds for drills are significantly simpler than those for mills, because there is only one operation that uses drills (namely, drilling), and drills are never moved horizontally inside the workpiece, so they don't need a horizontal feed setting. Just like mills, drills can have their feed and speed settings specified in more- or less-specific places, with more-specific settings overriding less-specific ones. In order from most specific to least, they are:

* `[drills.NNN]` is the most specific, applying to a single drill.
* `[[drills.sequence]]` applies to any drills created by that drill sequence.
* `[drills.defaults]` applies to all drills.

The list of settings that can be applied to drills are:

* `rpm` _(integer, rev/min):_ The rate at which the spindle spins, in revolutions per minute.
* `plunge` _(integer, mm/min):_ The rate at which the drill is plunged into the board vertically, in millimeters per minute.
* `plunge_max_ratio` _(integer, optional, (mm/min)/mm):_ To avoid breaking smaller drills, when computing the plunge rate to use, this number is multiplied by the diameter of the drill bit, and the result is used as the plunge rate if it is smaller. This leads to a lower plunge rate for thinner, more fragile bits that need more delicate handling. This ratio is measured in mm/min of plunge rate, per millimeter of bit diameter. This is intended for use in the `[[drills.sequence]]` and `[drills.defaults]` sections, but can be set in a `[drills.NNN]` section to override a more-general default if necessary.

## Tool selection

When invoking `pcb2gcode`, `pcb2cnc` will select appropriate tools for each operation. Typically, the default tool selection logic will come up with a usable, but possibly non-optimal, selection of tools for your board. You can always override the selections made by using the [`isolation.tools`](#cfg.isolation.tools), [`drilling.milldrill.tool`](#cfg.isolation.milldrill.tool), and [`routing.tool`](#cfg.routing.tool) options.

### Enabling and disabling operations for tools

In order to be used for an operation, the corresponding tool needs to be configured for that operation. If the tool's `[mills.NNN.<operation>]` block is present, the tool will always be enabled for that operation. Otherwise, endmills will be enabled for isolation, milldrilling, and routing, while other tools will only be enabled for isolation. These operations will use the [default feed/speed settings](#mill-feeds-and-speeds) defined in the `[mills.NNN]`, `[mills.defaults.<operation>]`, and `[mills.defaults]` blocks.

You can forcibly enable a tool for an operation, without defining specific feed/speed settings for the operation, by setting `<operation> = true` in the
`[mills.NNN]` block. Similarly, you can forcibly disable a tool for an operation,
even if it would otherwise be used, by setting `<operation> = false`. Note that due to TOML limitations, you can't set `<operation> = false` if you also have a
`[mills.NNN.<operation>]` block defined - you will need to also comment out that block if you want to disable that operation.

Example:

```toml
[mills.111]
ball_diameter = 1.590
isolation = false
routing = true

[mills.111.milldrill]
rpm = 15000

[mills.112]
diameter = 1.590
routing = false
```

In this example, tool 111 is enabled for routing, because `routing = true`, and for milldrill, because the `[mills.111.milldrill]` block is present. It is _not_ enabled for isolation, because `isolation = false`. Because tool 112 is an endmill, it is enabled for isolation and milldrill by default, and is explicitly disabled for routing.

### Default tool selections

For isolation, `pcb2cnc` will use all tools enabled for isolation, in decreasing order of radius. This will result in multiple isolation passes, starting with the largest tool to remove broad areas of copper, and ending with the smallest tool to remove finely-detailed areas.

For milldrilling, `pcb2cnc` will use the largest enabled tool that is smaller than the configured milldrill hole size. This will cut the milldrilled holes with the largest and most resilient tool.

For routing, `pcb2cnc` will use the same tool as milldrill, if milldrilling is enabled, and if routing is enabled for the milldrilling tool. Since routing comes immediately after milldrilling, this saves a tool change. If milldrilling is not enabled, or if the milldrilling tool cannot be used for routing, `pcb2cnc` will use the smallest enabled tool, to allow for the highest fidelity in concave corners of outlines.

## Other configuration

These settings control the overall behavior of `pcb2gcode`, or settings that apply across all milling operations.

### General configuration

These settings are configured at the top level of `pcb2cnc.toml`, before any `[block]` entries.

```toml
metric = true
zmove = 3.0
zchange = 5.0
zero_start = true
mirror_y = false
mirror_offset = 0.0
tiles_x = 1
tiles_y = 1
offset_x = 0.0
offset_y = 0.0
preamble = """
(=== generated by pcb2cnc ===)
"""
postamble = """
M2 (Program complete!)
%
"""
diameter_precision = 3
```

#### `metric` _(boolean, default true)_ <a id="cfg.metric"></a>

Controls whether input settings are specified in metric units. If false, settings use imperial units (inches) instead.

Corresponds to the `metric` option of `pcb2gcode`.

#### `zmove` _(decimal (mm), default 3.0)_ <a id="cfg.zmove"></a>

The Z-height above program zero at which the machine moves between operations. This should be set high enough to clear the workpiece, but not so high as to cause long, time-consuming Z movements between cuts.

Corresponds to the `zsafe` option of `pcb2gcode`.

#### `zchange` _(decimal (mm), default `zmove`)_ <a id="cfg.zchange"></a>

The Z-height above program zero to which the machine is moved before changing tools. If unset, this is set to `zmove`, since the machine will always be at movement height after it is finished with a tool. If your machine doesn't automatically retract the toolhead to a safe tool-change height when it receives an `M6` tool-change command, set this to a number that will allow you to change the tool.

Corresponds to the `zchange` option of `pcb2gcode`.

#### `zero_start` _(boolean, default true)_ <a id="cfg.zero_start"></a>

If `true`, the output G-code is translated so that the leftmost edge of the board is at X = 0, and the lowest edge of the board is at Y = 0. Otherwise, the coordinates from the Gerber file are used, which may result in your program zero being somewhere unexpected.

Corresponds to the `zero-start` option of `pcb2gcode`.

#### `mirror_y` _(boolean, default false)_ <a id="cfg.mirror_y"></a>

Controls how the board is mirrored when cutting the back side of the board. If true, the board is flipped along the Y-axis (mirrored top to bottom). If false, the board is flipped along the X-axis (mirrored left to right). Defaults to flipping the board left-to-right.

Corresponds to the `mirror-yaxis` option of `pcb2gcode`.

#### `mirror_offset` _(decimal (mm), default 0.0)_ <a id="cfg.mirror_offset"></a>

The coordinate of the line along which the board is mirrored to cut the back side. If [`mirror_y`](#cfg.mirror_y) is true, this is a Y-coordinate; otherwise, it is an X-coordinate. By default, this is 0.0, which, along with the default `mirror_y = false`, will result in back-side cuts zeroing at the bottom-right side of the board, and proceeding in the -X direction.

Corresponds to the `mirror-axis` option of `pcb2gcode`.

#### `tiles_x` _(positive integer, default 1)_ <a id="cfg.tiles_x"></a><br>`tiles_y` _(positive integer, default 1)_ <a id="cfg.tiles_y"></a>

Controls how multiple copies of the board are tiled onto a single PCB blank. Copies will be laid out in a grid pattern `tiles_x` copies wide by `tiles_y` copies tall. All copies will be cut at once, so if you have a large enough PCB blank and need to make multiple copies of the board, this will allow you to cut them all at once while minimizing the number of tool changes.

Corresponds to the `tile-x` and `tile-y` options of `pcb2gcode`.

#### `offset_x` _(decimal (mm), default 0.0)_ <a id="cfg.offset_x"></a><br>`offset_y` _(decimal (mm), default 0.0)_ <a id="cfg.offset_y"></a>

Offsets the created G-code coordinates by the specified amount. You can use this to place your program zero somewhere other than at the corner of the board (with [`zero_start` = true](#cfg.zero_start)) or the Gerber origin (without it).

Corresponds to the `x-offset` and `y-offset` options of `pcb2gcode`.

#### `preamble` _(string, default empty)_ <a id="cfg.preamble"></a><br>`postamble` _(string, default empty)_ <a id="cfg.postamble"></a>

Allows specifying a G-code snippet that will be inserted before or after your completed program. You can use this to zero the machine, turn accessories on and off, or for any other setup and teardown task you might need. If you want to specify multiple instructions, use a multi-line string.

Note that this string will be inserted _verbatim_ at the beginning or end of the generated G-code file. It is not checked for validity in any way!

#### `diameter_precision` _(integer, default 3)_ <a id="cfg.diameter_precision"></a>

Controls the number of decimal places to which the diameter of each cutting tool is specified. If you use `pcb2cnc millproject` to generate a `pcb2gcode` config file, this setting must be the same between `pcb2cnc millproject` and `pcb2cnc postprocess`, so that `pcb2cnc` can properly recognize the tools used in the generated G-code files.

Defaults to 3 decimal places of precision, which should be sufficient for most purposes.

### Optimization configuration

These settings control how the resulting G-code program is optimized and planned.

```toml
[optimize]
tolerance = 0.02
gcode_optimize = 0.0001
eulerian_paths = true
tsp_2opt = true
pathfinding_steps_limit = 3
g0_vertical_speed = 1270.0
g0_horizontal_speed = 2540.0
backtrack = 0.0
```

#### `tolerance` _(decimal (mm), default 0.01)_ <a id="cfg.optimize.tolerance"></a>

The maximum amount of allowable deviation from the commanded toolpath, in millimeters. This is used for some calculations internal to `pcb2gcode` to ensure that areas that shouldn't intersect don't, even if the toolhead position is slightly off.

Corresponds to the `tolerance` option of `pcb2gcode`.

#### `gcode_optimize` _(decimal (mm), default 0.00254)_ <a id="cfg.optimize.gcode_optimize"></a>

Simplifies the resulting G-code, allowing a deviation from the exact coordinates of up to this amount. Even a small value here can result in much smaller G-code, without appreciable changes in the resulting board.

Corresponds to the `optimize` option of `pcb2gcode`.

#### `eulerian_paths` _(boolean, default true)_ <a id="cfg.optimize.eulerian_paths"></a>

Skips milling over a line multiple times if the lines overlap. Can save up to 50% of milling time, so leave this on unless you know you need it off.

Corresponds to the `eulerian-paths` option of `pcb2gcode`.

#### `tsp_2opt` _(boolean, default true)_ <a id="cfg.optimize.tsp_2opt"></a>

Uses the [2-opt algorithm](https://en.wikipedia.org/wiki/2-opt) to optimize the generated G-code toolpath. This results in a toolpath that is faster to run, but slows down G-code generation.

Corresponds to the `tsp-2opt` option of `pcb2gcode`.

#### `pathfinding_steps_limit` _(positive integer, default 1)_ <a id="cfg.optimize.pathfinding_steps_limit"></a>

Controls how many steps ahead the G-code pathfinding algorithm will look when optimizing its output toolpath. A higher number results in a faster toolpath, but slower G-code generation.

Corresponds to the `path-finding-limit` option of `pcb2gcode`.

#### `g0_vertical_speed` _(decimal (mm/min), default 1270.0)_ <a id="cfg.optimize.g0_vertical_speed"></a><br>`g0_horizontal_speed` _(decimal (mm/min), default 2540.0)_ <a id="cfg.optimize.g0_horizontal_speed"></a>

Specifies the speed of `G0` non-cutting G-code movements, in the horizontal (X) and vertical (Y) axis directions. This does _not_ control the actual speed of those movements in the G-code, but is used as an input to optimize toolhead movement. Set this as close to your machine's top `G0` movement speed as you can.

Corresponds to the `g0-vertical-speed` and `g0-horizontal-speed` options of `pcb2gcode`.

#### `backtrack` _(decimal (mm/min), default 0.0)_ <a id="cfg.optimize.backtrack"></a>

Controls the tradeoff between backtracking along existing cut lines and performing a retract-move-plunge cycle. Set this to the amount of remilling you're willing to accept, in millimeters remilled per minute saved. For example, specifying `500` here would mean `pcb2gcode` can remill 500mm of path length if it would save 1 minute or more.

Corresponds to the `backtrack` option of `pcb2gcode`.
