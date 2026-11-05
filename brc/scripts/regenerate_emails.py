"""Rewrite dispute emails with Grok. Refuses to run without XAI_API_KEY.

The checked-in emails are the offline corpus. This script replaces them only
when someone chooses to regenerate.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EMAILS = ROOT / "data" / "emails"


def main() -> int:
    key = os.environ.get("XAI_API_KEY", "").strip()
    if not key:
        print("XAI_API_KEY assente: le email già nel repository restano la fonte offline.", file=sys.stderr)
        return 2
    prompts = json.loads((EMAILS / "prompts.json").read_text(encoding="utf-8"))
    model = os.environ.get("XAI_MODEL", "grok-4")
    base = os.environ.get("XAI_BASE_URL", "https://api.x.ai/v1").rstrip("/")
    for item in prompts["prompts"]:
        existing = json.loads((EMAILS / item["file"]).read_text(encoding="utf-8"))
        body = json.dumps(
            {
                "model": model,
                "temperature": 0,
                "messages": [
                    {
                        "role": "system",
                        "content": "Rispondi solo con JSON {\"subject\":\"...\",\"body\":\"...\"}. Italiano, ufficio acquisti.",
                    },
                    {"role": "user", "content": item["prompt"]},
                ],
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{base}/chat/completions",
            data=body,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=45) as response:
            payload = json.loads(response.read().decode("utf-8"))
        content = payload["choices"][0]["message"]["content"]
        start, end = content.find("{"), content.rfind("}")
        parsed = json.loads(content[start : end + 1])
        existing["subject"] = parsed["subject"]
        existing["body"] = parsed["body"]
        existing["source"] = "grok"
        (EMAILS / item["file"]).write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(item["file"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
