# Protocol notes — soundcore Life Q30

What the Q30 speaks, and how it differs from the two Soundcore models upstream
supports. The sections below are kept from [Omaphones](https://github.com/ncr/omarchy-headphones),
whose author probed the Space 2 and wrote the framing down; the Life Q30
section is this fork's.

## Soundcore Space 2 — vendor RFCOMM

Notes from probing a **Soundcore Space 2** (`84:9D:4B:B0:2D:00`, modalias
`bluetooth:v02B0p0000d001F`).

Like Sony and Xiaomi, its listening mode is over an RFCOMM channel. Soundcore devices
advertise a vendor UUID starting with `0cf12d31-fac3-4553-bd80-d6832e7...`
(`0cf12d31-fac3-4553-bd80-d6832e7d1402` on the Space 2, where `d1402` is the model ID).
Connecting an `org.bluez.Profile1` to this UUID opens the RFCOMM control link.

### Framing

```
outbound:  08 ee 00 00 00 <cmd u8, u8> <len u16le> <payload> <checksum u8>
inbound:   09 ff 00 00 01 <cmd u8, u8> <len u16le> <payload> <checksum u8>
```

- `<len u16le>` is the little-endian total byte length of the packet (including header, command, length, payload, and checksum).
- `<checksum u8>` is the simple 8-bit sum (`sum(packet[:-1]) & 0xFF`).

### Commands

| Command | Direction | Meaning |
|---|---|---|
| `01 01` | Outbound | `RequestState`: ask for full initial state |
| `01 01` | Inbound | `StateUpdate`: 103-byte payload (113-byte packet), sound modes at offset 71..77 |
| `06 81` | Outbound | `SetSoundModes`: 6-byte payload `[mode, custom_anc, transparency_mode, nc_mode, wind_noise, custom_transparency]` |
| `06 81` | Inbound | ACK from headphones |
| `06 01` | Inbound | `SoundModes` notification when mode changes |

### Sound mode byte

- `0x00` — ANC
- `0x01` — Ambient
- `0x02` — Off (Normal)

A mode change is confirmed on the headphones immediately and answered with both an ACK (`06 81`) and an unsolicited notification (`06 01`).

### The probe

[`tools/soundcore_probe.py`](tools/soundcore_probe.py):

```bash
tools/soundcore_probe.py 84:9D:4B:B0:2D:00
tools/soundcore_probe.py 84:9D:4B:B0:2D:00 5 set:ambient
```



### Space One Pro (A3062) — the same protocol, six bytes further left

Notes from a **soundcore Space One Pro** (`7C:E9:13:2C:2B:6D`, firmware
`0.4.3.9`). Same vendor channel as the Space 2, same framing, same commands.
One thing differs, and it is enough to break both reading and writing.

#### The block moves

`0cf12d31-fac3-4553-bd80-d6832e7b3062`, model ID `b3062` in the usual place, and
`01 01` answers with a 95-byte state. The six sound mode bytes are **at offset
69, not 71**:

```
... 04 04 0f 03 02 05 31 01 31 01 31 01 00 00 01 ...
          ^^ ^^ ^^^^^^^^^^^^^^^^^ the block, at 69
          |  ambient sound mode cycle
          press twice
```

Two bytes to the left of where the Space 2 keeps it. Which field accounts for
the difference I have not established — there is no Space 2 here to compare
against — but 69 is not a guess about this one headset. It falls out of
[OpenSCQ30](https://github.com/Oppzippy/OpenSCQ30)'s A3062 parser, which reads
the packet field by field, and every field ahead of the sound modes has a fixed
width:

```
  0..1   battery              23..34  equalizer configuration
  2..6   firmware version     35..36  unknown
  7..22  serial number        37..64  custom hear id, music genre at the end
                              65..66  unknown
                              67      button configuration
                              68      ambient sound mode cycle
                              69      sound modes  <-
```

That layout was derived independently, from a different unit, and it lands on
the same six bytes this one reports. A firmware that moved them would have to
change one of those widths, and would break OpenSCQ30 in the same breath.

Read at 71 the mode comes out as `0x31`, which is no mode at all, and the row
stays on **pending** forever:

```
$ tools/soundcore_probe.py 7C:E9:13:2C:2B:6D 20
<<< STATE: mode=unknown(49) params=310131013101
```

The quieter half of the same bug is the write. `set` takes
`sound_mode_params`, replaces byte 0 and sends the rest back — so with the
offset wrong it posts five of the device's neighbouring settings as if they were
the mode parameters. On this headset that overwrote the custom noise cancelling
and custom transparency levels with `31 01 31 01`, which is not a value anyone
chose.

#### Ask 06 01 and no offset is needed

`06 01` answers with the six bytes and nothing around them — on this model;
whether the Space 2 answers a request is untested, it has only been seen to emit
one unprompted:

```
>>> 08 ee 00 00 00 06 01 0a 00 07
<<< 08 ee 00 00 00 06 01 10 00 02 50 01 01 00 05 ...
                              ^^^^^^^^^^^^^^^^^ exactly body[69:75] above
```

`on_packet` already reads that reply correctly. It was simply never asked for —
the handshake sends `01 01` and waits. Asking as well costs one 10-byte frame at
connect and cannot drift as firmware moves fields about.

Worth saying why the offset is not simply checked instead. The obvious guard is
to take the byte at 71 only when it looks like a mode and ask `06 01` otherwise,
and it does not work: the neighbours are switches, so the byte at 71 reads as a
perfectly plausible `0`, `1` or `2` most of the time. On this headset with the
mode on Ambient it is `01`, and a guard like that reports Ambient for the wrong
reason and then writes the wrong five bytes back, exactly as before. Tried, and
it took a second round of overwritten levels to notice.

So the query goes out after the state packet, and its reply stands over
whatever the offset produced. Which models are asked is a row in `MODELS` at the
top of `soundcore-bridge`: the Space One Pro, and any model the bridge has not
seen. The Space 2's row says not to — its owner has not confirmed a request is
answered, and a headset that works is not sent a frame it has never been seen
to take. `tests/soundcore_bridge_test.py` pins each row's frames, so the next
model cannot change what an earlier one is sent without failing there.

#### And ask again after a set

The Space 2 answers a set with an ACK **and** an unsolicited `06 01`, which is
what carries the row along. The Space One Pro sends the ACK alone. The write
lands — reading back over the vendor channel confirms it, tail intact:

```
$ omarchy-shell q30 setMode anc
ok
<<< 06 01 body=00 50 01 01 00 05        mode 0, and the five neighbours untouched
```

— but nothing arrives to say so, so the panel goes on showing the mode the
headphones were in a moment ago. One `06 01` after each write settles it.

#### A caution about the vendor channel going missing

Worth writing down because it cost an afternoon. After several hours connected,
and an auto power off in the middle, `ConnectProfile` began refusing the vendor
UUID outright:

```
09:38:40.594 ConnectProfile: br-connection-not-supported
...eleven times, once per retry...
```

[OpenSCQ30](https://github.com/Oppzippy/OpenSCQ30) v2.11.0 failed identically at
the same call, and a scan of RFCOMM channels 1-30 found only HFP, the Message
Stream, and two channels that accept a socket and answer nothing. It reads
exactly like a device that does not serve the channel.

**It is transient.** Power cycling the headphones brought it straight back, and
it has been reliable since. `EXIT_TRANSIENT` and the growing backoff are the
right answer; anything that parks the address on that error would leave the row
disabled until the shell restarts, at the very moment a power cycle would have
fixed it.

In that state the control protocol is also reachable over BLE GATT, on the
rotating Fast Pair address — service `0179f5da-0000-1000-8000-00805f9b34fb`,
write handle `0x001a`, notify handle `0x0016`, requests keeping the `08 ee`
header and replies using `09 ff 00 00 01`. `06 01` works there too and returns
the same six bytes; `06 81` is acknowledged and reads back changed. Recorded in
case it is ever the only way in, though on a healthy device the vendor channel
is simpler and this plugin needs nothing from it. Two notes for anyone who
tries: the address rotates and arrives unprompted as `0b 02` on the same notify
handle, and BlueZ drops the LE link the moment no client holds it.

### Life Q30 (A3028) — the same protocol, four bytes at 35

Notes from a **soundcore Q30** (`88:0E:85:5F:64:B4`, firmware `05.24`, serial
starting `3028`). Same vendor channel as the Space 2, same framing, same
commands, same mode bytes. Two things differ, and between them the row never
said anything at all.

#### A shorter state, and the block near its front

`0cf12d31-fac3-4553-bd80-d6832e7b302a`, model ID `b302a`, and `01 01` answers
with a **70-byte** state — a third of the Space 2's. The sound mode bytes are at
**offset 35**:

```
0200 fefe9d93949faa8d8f78 0000...0000 01 00 01 00 00 30352e32343330 3238...
                                         ^^^^^^^^^^^ the block, at 35
                                      ^^ ambient sound mode cycle
                                                     ^^^^^^^^^^^^^ "05.24" — firmware,
                                                                   as ASCII, from 39
```

Read at 71 there is no byte at all: the payload ends at 69. `on_packet` needs
`len(body) >= offset + 4` before it reads anything, so the state was discarded,
`06 01` was never asked for on the UNKNOWN row's terms, and the bridge sat
connected and silent until the service parked the address. From outside, the
headphones showed a battery and no controls.

#### The block is four bytes wide, and what follows is not mode data

This is the part that matters beyond this one headset. The Space 2's block is
six bytes and the bridge reads six. Here the fourth byte is the last:

```
>>> 08 ee 00 00 00 06 01 0a 00 07              request
<<< 09 ff 00 00 01 06 01 0e 00 00 01 00 00 ..  four bytes, not six
```

Byte 39 is `0x30`, the `0` of `05.24`. A six-byte read takes `30 35` along with
the block, and `set` posts them straight back as mode parameters — the same
failure the Space One Pro section describes, one field further along. Observed
here, with the bridge's default parameters rather than a mis-offset read:

```
before  ... 01 00 01 00 00 30 35 ...     byte 36 = 01, byte 37 = 00
write   08 ee 00 00 00 06 81 10 00 01 1f ff 00 00 01 ad
after   ... 01 01 1f ff 00 30 35 ...     byte 36 = 1f, byte 37 = ff
```

`1f ff` are the bridge's defaults for a model that keeps a custom transparency
level there. This one does not: the write left two fields holding values nobody
chose, and restoring them took a second write with the bytes read before the
first. A four-byte write, carrying the device's own parameters with only the
mode replaced, is accepted and changes nothing else:

```
>>> 08 ee 00 00 00 06 81 0e 00 01 01 00 00 8d
<<< ACK, and the state reads back 01 01 00 00 — mode changed, neighbours intact
```

So the model row carries a width, and the rows that came before say six.

#### The rest of the state: battery, equalizer, grade

Field by field, following OpenSCQ30's A3028 parser, which lands on the sound
modes at 35 exactly as the probe did:

```
  0      battery level, 0-5          24..34  hear ID (not used)
  1      charging, 0/1               35      mode (0 anc, 1 ambient, 2 off)
  2..3   equalizer preset, u16 LE    36      ANC grade (0 transport, 1 outdoor,
  4..11  eight gains, dB x10 + 120            2 indoor, 3 custom)
  12     gender (not used)           37..38  the rest of the block
  13     age range (not used)        39..    firmware "05.24", then serial
```

The equalizer is written with `02 81` and the same ten bytes. `fefe` is the
custom preset; the 22 others carry fixed gains, and sending one makes the device
drop the custom curve it held. Verified on this unit: Bass Booster read back as
its own id, and the saved curve sent under `fefe` read back as Custom.

The grade was written and read back for each of 0x00, 0x02 and 0x03. It is kept
across modes, so a line carries it whatever the mode is.


#### No dial, no switch

The Q30 has no ambient level and no wind noise reduction, and the four bytes
hold neither. The row reports `mode` alone; `DeviceFollower` reads a missing
`level` as -1 and draws no dial, which is what it already does for the JBL.

#### What was verified on the hardware

Off, ANC and Ambient were each set from the shell and read back from the device,
and the mode the headphones reported afterwards matched every time. Battery
continues to come from Fast Pair. Untested: charging state, the noise-cancelling
sub-mode at byte 36 (transport / outdoor / indoor / custom in OpenSCQ30's A3028
parser, and writable — that is how `1f` landed there), and the equalizer, which
lives on a command this bridge does not send.

