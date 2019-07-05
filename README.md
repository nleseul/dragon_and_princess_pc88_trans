# Dragon & Princess (PC-88) Translation patch

This is a translation patch for a simple
Japanese NEC-BASIC game called *Dragon &
Princess*, generally assumed to have been
released in late 1982. This game is notable
for having been the first known RPG by
Japanese developers, and for being the
first known RPG with multi-character
tactical combat (which *Ultima III* would
introduce several months later).

I (NLeseul) looked into making a 
translation patch for this game 
specifically because of rumors that
[the CRPG Addict](http://crpgaddict.blogspot.com)
was interested in a playthrough. Much of
the translation was provided by Laszlo
Benyi, another regular on that site's 
forums who volunteered.

## Applying the patch

This patch will be distributed in BPS
format, so you will need a patching tool
capable of handling it.
[Floating IPS](https://www.romhacking.net/utilities/1040/)
is a fairly common such tool, and is also
available on [GitHub](https://github.com/Alcaro/Flips). Romhacking.net
also maintains an [online patching tool](https://www.romhacking.net/patch/).

The only known version of *Dragon &
Princess* is on a compilation disk image
with the filename Oldmix2.d88, so that
is the file to which the patch should be
applied.

CRC32: `798b6721`

Note that the original disk also contained
a game called *トンキーゴリラ* ("Donkey Gorilla").
It didn't boot at all for me on the M88
emulator, so it may simply be lost to
history. Hence, I elected to have the patch
overwrite that game's data to make room
for the English text of *Dragon & Princess*.
So if you're particularly attached to your
copy of *トンキーゴリラ*, you should
probably back it up before applying this
patch.

All other games on the disk should be
untouched, so you can continue enjoying
such gems as *The Pro Bowling*, *スカートめくり*
("Skirt Lift"), *3D-Tank Conbat*, and
*MOMOKO チャン ノ サクランボ ヒロイ ゲーム*
("Momoko-chan's Cherry-Grabbing Game").

## Building the patch

If for some reason you want to build the
patch yourself using the script from GitHub,
the syntax is simply:

```
python build_patch.py <original disk> <destination disk>
```

Note that if the destination disk already
exists, it will be overwritten.

### Building an easy mode disk

The script can also generate a disk with
the party's initial stats maxed out and
random encounters disabled, to make testing
easier. Simply include the `--easy-mode`
flag in the above command line.

## Contact

This patch's source is hosted on 
[GitHub](https://github.com/nleseul/dragon_and_princess_pc88_trans);
questions and bugs can easily be placed
there. You can also reach me directly
at [nleseul@this-life.us](mailto:nleseul.this-life.us).