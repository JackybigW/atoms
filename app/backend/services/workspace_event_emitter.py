from dataclasses import dataclass
from inspect import isawaitable
from pathlib import Path
from typing import Any, Awaitable, Callable


EventSink = Callable[[dict[str, object]], Any]


@dataclass(slots=True)
class WorkspaceEventEmitter:
    event_sink: EventSink

    async def emit(self, event: dict[str, object]) -> None:
        result = self.event_sink(event)
        if isawaitable(result):
            await result

    async def assistant_delta(self, content: str, *, agent: str = "swe") -> None:
        await self.emit({"type": "assistant.delta", "agent": agent, "content": content})

    async def progress(self, label: str) -> None:
        await self.emit({"type": "progress", "label": label})

    async def terminal_log(self, content: str) -> None:
        await self.emit({"type": "terminal.log", "content": content})

    async def file_snapshot(self, *, root: Path, file_path: Path, content: str) -> None:
        await self.emit(
            {
                "type": "file.snapshot",
                "path": file_path.relative_to(root).as_posix(),
                "content": content,
            }
        )
