"""What soundcore-bridge sends to each model in MODELS.

    python -m unittest tests.soundcore_bridge_test

The frozen sessions — one per MODELS row, somebody's working headphones — are
the pin files in tests/pins/soundcore/. What is here is the model lookup and
the behaviour of UNKNOWN, the one row that may change.

No hardware and no D-Bus: the bridge's only two effects on the world are
`write()` and `emit()`, and both are captured.
"""
import unittest

from tests import harness

bridge_module = harness.load_bridge("soundcore-bridge")

INBOUND_HDR = bytes([0x09, 0xFF, 0x00, 0x00, 0x01])


def inbound(cmd, body):
    """A device frame, the mirror of make_packet."""
    total = 5 + 2 + 2 + len(body) + 1
    raw = INBOUND_HDR + bytes(cmd) + bytes([total & 0xFF, total >> 8]) + bytes(body)
    return raw + bytes([bridge_module.calc_checksum(raw)])


def state_payload(length, offset, six):
    """A 01 01 state payload with the six sound-mode bytes at `offset`.

    Everything else is 0x31: a plausible switch value, which is what makes a
    wrong offset read as a plausible mode — the trap PROTOCOL.md describes.
    """
    body = bytearray([0x31] * length)
    body[offset:offset + 6] = bytes(six)
    return bytes(body)


class Session(harness.Session):
    """A Soundcore session. "device" in a pin is {"cmd": "06 01", "body": hex}
    — the command pair and the body, wrapped the way the device wraps them.
    "sent" is every whole frame the bridge wrote, as hex."""

    def __init__(self, uuid):
        super().__init__(bridge_module)
        self.bridge = bridge_module.Bridge(None, "84:9D:4B:B0:2D:00", harness.FakeLoop())
        self.bridge.model = bridge_module.model_for(uuid)
        self.bridge.write = self.frames.append

    def receive(self, frame):
        self.bridge.buffer += frame
        self.bridge.parse_buffer()

    def device(self, spec):
        self.receive(inbound(harness.hexbytes(spec["cmd"]),
                             harness.hexbytes(spec.get("body", ""))))


harness.pin_tests(globals(), "soundcore-bridge", Session)


class ModelLookup(unittest.TestCase):
    Q30 = "0cf12d31-fac3-4553-bd80-d6832e7b302a"

    def test_q30_by_uuid_suffix(self):
        self.assertEqual(bridge_module.model_for(self.Q30)["name"], "Life Q30")

    def test_case_insensitive(self):
        self.assertEqual(bridge_module.model_for(self.Q30.upper())["name"], "Life Q30")

    def test_another_soundcore_falls_back_to_the_one_row(self):
        """This fork carries one model, so anything else is read on the Q30's
        terms. That is wrong for another headset, and deliberately so: upstream
        keeps the rows for the Space 2 and the Space One Pro."""
        self.assertIs(bridge_module.model_for("0cf12d31-fac3-4553-bd80-d6832e7ffff0"),
                      bridge_module.MODELS["b302a"])

    def test_every_pinned_model_has_a_row(self):
        for _path, pin in harness.pins_for("soundcore-bridge"):
            row = bridge_module.model_for(pin["session"]["uuid"])
            self.assertEqual(row["name"], "Life Q30", pin["model"])


class ShortState(unittest.TestCase):
    def test_short_state_is_unsupported(self):
        """Shorter than the block's offset: nothing to read, and the service is
        told so rather than left waiting."""
        s = Session(ModelLookup.Q30)
        s.receive(inbound((0x01, 0x01), bytes(20)))
        # The row asks anyway -- 06 01 is where this model's answer comes from --
        # and the state having nothing readable is what marks it unsupported.
        self.assertEqual(s.sent, ["08 ee 00 00 00 06 01 0a 00 07"])
        self.assertEqual(s.bridge.exit_code, bridge_module.EXIT_UNSUPPORTED)


class QueryStandsOver(unittest.TestCase):
    """The reply to 06 01 is what the panel is told, whatever the state held."""

    UUID = ModelLookup.Q30
    FOUR = [0x02, 0x01, 0x00, 0x00]          # off, as the device answers
    STATE = state_payload(70, 35, [0x00, 0x01, 0x00, 0x00])   # anc, in the state

    def test_reply_stands_over_the_state(self):
        s = Session(self.UUID)
        QUERY = bridge_module.CMD_SOUND_MODES_NOTIFY
        SET = bridge_module.CMD_SOUND_MODES_SET
        make = bridge_module.make_packet

        s.receive(inbound((0x01, 0x01), self.STATE))
        self.assertEqual(s.lines[0]["mode"], "anc")
        self.assertEqual(s.frames, [make(QUERY)])
        s.receive(inbound((0x06, 0x01), self.FOUR))
        self.assertEqual(s.lines[-1]["mode"], "off")
        # The write is four bytes: the device's own parameters, mode replaced.
        s.command("set anc")
        self.assertEqual(s.frames[-1], make(SET, bytes([0x00, 0x01, 0x00, 0x00])))
        self.assertEqual(s.timers, [400])

    def test_no_dial_and_no_switch(self):
        """Four bytes hold neither, so the row says neither and the panel
        draws neither."""
        s = Session(self.UUID)
        s.receive(inbound((0x01, 0x01), self.STATE))
        self.assertNotIn("level", s.lines[0])
        self.assertNotIn("voice", s.lines[0])

    def test_level_and_voice_are_ignored(self):
        s = Session(self.UUID)
        s.receive(inbound((0x01, 0x01), self.STATE))
        before = list(s.frames)
        s.command("level 3")
        s.command("voice on")
        self.assertEqual(s.frames, before)


if __name__ == "__main__":
    unittest.main()
