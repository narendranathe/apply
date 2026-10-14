import copy
import json
from pathlib import Path
import unittest

from cloud.intake_transport import generate_keys, seal, unseal


class IntakeTransportTests(unittest.TestCase):
    def test_roundtrip_private_payload_never_enters_workflow_input(self):
        private, public = generate_keys()
        source = {'message': 'SYNTHETIC PRIVATE MESSAGE', 'actor': 'fixture-agent'}
        first, second = seal(source, public), seal(source, public)
        self.assertEqual(unseal(first, private), source)
        self.assertNotEqual(first, second)
        self.assertNotIn(source['message'], json.dumps(first))
        workflow = Path('.github/workflows/agent-intake.yml').read_text()
        self.assertNotIn('inputs.envelope }}', workflow)
        self.assertIn('inputs.envelope_ciphertext', workflow)
        self.assertIn('secrets.AGENT_INTAKE_PRIVATE_KEY', workflow)

    def test_tampering_wrong_key_and_oversize_fail(self):
        private, public = generate_keys()
        other, _ = generate_keys()
        sealed = seal({'message': 'private'}, public)
        for altered, key in ((sealed, other), ({**sealed, 'nonce': 'a' * 16}, private),
                             ({**sealed, 'schema': 'wrong'}, private)):
            with self.assertRaises(Exception):
                unseal(altered, key)
        with self.assertRaises(ValueError):
            seal({'message': 'a' * 32001}, public)
