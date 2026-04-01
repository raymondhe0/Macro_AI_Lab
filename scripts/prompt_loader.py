"""
Prompt Loader — reads prompts from prompts/<category>/<name>.md at runtime.
To tune a prompt: open the .md file in VS Code and edit. No Python changes needed.
"""

from pathlib import Path


class PromptLoader:
    BASE = Path(__file__).parent.parent / "prompts"

    @staticmethod
    def load(category: str, name: str) -> str:
        """Load prompts/category/name.md and return its contents as a string."""
        path = PromptLoader.BASE / category / f"{name}.md"
        if not path.exists():
            raise FileNotFoundError(
                f"Prompt not found: {path}\n"
                f"Available prompts in '{category}': "
                + str([p.stem for p in (PromptLoader.BASE / category).glob("*.md")])
            )
        return path.read_text(encoding="utf-8").strip()
