# Mini DGUS fork

Modified from Desuuuu's DGUS T5UID1 fork to support Mini DGUS displays.

The Mini DGUS display (DGUSM) is found on the Wanhao Duplicator i3+ and
several other printers.  It does not support all of the functionality as the
t5uid1, but otherwise is fairly similar.  Specifically, this is for the
DMT48270M043_02WT model, which is the low end DGUSM display.

Main differences:
- System control is through 256 bytes of "register" space which uses
  separate register read/write commands.
- No ACK is sent for writes, just reads.
- CRC is not supported (not being used on T5UID1 currently though)
- Only 4KB of RAM (for VPs) instead of 128KB.  None of it is reserved as
  opposed to 8KB being reserved for System on T5UID1, and more if curve
  display is desired.
- No ability to enable/disable/update "touch" controls at runtime.  Can only
  update "variable" configuration at runtime through SP (also can be done
  with T5UID1).
- Beeper instead of wav file audio playback
- Volume is not adjustable
- Slower LCD refresh rate (200ms instead of 40ms)
- No negative font kerning.
- Maximum icon size of 255px x 255px as opposed to 1023px x 1023px.
- No 8-byte long int variable support.
- Different CONFIG.txt file is needed on SD card.

Otherwise, the displays are fairly similar.  The resolution is the same
(480x272), config files and data format for touch and variables are the
same, and the same tools can be used.

NOTE: the code still says t5uid1 currently, but only supports DGUSM.

-------------------------------

Welcome to the Klipper project!

[![Klipper](docs/img/klipper-logo-small.png)](https://www.klipper3d.org/)

https://www.klipper3d.org/

Klipper is a 3d-Printer firmware. It combines the power of a general
purpose computer with one or more micro-controllers. See the
[features document](https://www.klipper3d.org/Features.html) for more
information on why you should use Klipper.

To begin using Klipper start by
[installing](https://www.klipper3d.org/Installation.html) it.

Klipper is Free Software. See the [license](COPYING) or read the
[documentation](https://www.klipper3d.org/Overview.html).
