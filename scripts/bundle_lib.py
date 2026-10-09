#!/usr/bin/env python3
"""Re-embed lib/wcp_agent_lib.py into every agent that carries a copy of it.
Run after editing the library, then bump the agents' versions."""
import base64
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BLOB = re.compile(r"(b64decode\(')[A-Za-z0-9+/=]+('\), _agent_lib\.__dict__)")

blob = base64.b64encode((ROOT / "lib" / "wcp_agent_lib.py").read_bytes()).decode()
for script in sorted((ROOT / "agents").glob("*/agent.*")):
    text = script.read_text()
    new = BLOB.sub(lambda m: m.group(1) + blob + m.group(2), text)
    if new != text:
        script.write_text(new)
        print("updated", script.relative_to(ROOT))
