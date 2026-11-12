"""Agent contract for CogniSphere Labs.

Every department lives in its own file and only needs:

    AGENT_SPEC = {"id": "script_writing", "name": "SCRIPT WRITING",
                  "role": "Plot & Dialogue", "accent": "#7c9cff"}
    def build(bus, keys) -> AgentBase: ...

Modules are discovered dynamically by ``core.registry`` — a missing file becomes a
"pending module" placeholder instead of crashing the app.
"""
