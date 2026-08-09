import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


CLIENT_PATH = (
    Path(__file__).resolve().parents[2]
    / ".agents"
    / "skills"
    / "datatube"
    / "scripts"
    / "datatube_client.py"
)


def load_client_module():
    spec = importlib.util.spec_from_file_location("datatube_client_under_test", CLIENT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class DataTubeClientPayloadTests(unittest.TestCase):
    def test_parse_json_arg_accepts_inline_object(self):
        client = load_client_module()
        self.assertEqual({"title": "BTC Alpha"}, client.parse_json_arg('{"title":"BTC Alpha"}'))

    def test_parse_json_arg_accepts_utf8_json_file(self):
        client = load_client_module()
        with tempfile.TemporaryDirectory() as directory:
            payload_path = Path(directory) / "project.json"
            payload_path.write_text(
                json.dumps({"title": "BTC 金叉 Alpha"}, ensure_ascii=False),
                encoding="utf-8",
            )
            self.assertEqual(
                {"title": "BTC 金叉 Alpha"},
                client.parse_json_arg(str(payload_path)),
            )


if __name__ == "__main__":
    unittest.main()
