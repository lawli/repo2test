import click

from apitest.cli import cli


def _commands(group: click.Group, prefix: str = "") -> list[tuple[str, click.Command]]:
    found = []
    for name, command in group.commands.items():
        found.append((prefix + name, command))
        if isinstance(command, click.Group):
            found.extend(_commands(command, f"{prefix}{name} "))
    return found


def test_every_command_describes_itself_in_help() -> None:
    assert [name for name, command in _commands(cli) if not command.help] == []
