"""Start an isolated browser test server; never sends a provider request.

Run `python -m tests.browser_fixture`, then visit http://127.0.0.1:8790.
The visibly labeled stub deliberately delays and returns fractional inputs to
exercise input preservation and asynchronous UI state. Data lives in ignored
test-results/browser-fixture, separate from the operator's run archive.
"""

from pathlib import Path
from time import sleep

from worldfork.engine import validate_scenario
from worldfork.server import create_server


class StubInterpreter:
    configured = True
    model = "TEST STUB — no external requests"

    def interpret(self, prompt):
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("Enter a test description.")
        sleep(2)
        return {
            "scenario": validate_scenario({
                "name": "Precision <b>fixture</b>",
                "demand_multiplier": 1.17,
                "disruption": 0.43,
                "reserve_units": 13.25,
                "seed": 137,
                "worlds": 64,
            }),
            "interpretation": "Fixed browser fixture. Check fractional inputs before running.",
            "provider": "local-test-stub",
        }


if __name__ == "__main__":
    server = create_server(port=8790, data_dir=Path("test-results/browser-fixture"), provider=StubInterpreter())
    print("TEST FIXTURE ONLY: http://127.0.0.1:8790 — no external model requests", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
