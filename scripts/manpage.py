#!/usr/bin/env python3
"""Generate the lbctl(1) man page from the argparse parser.

The man page is generated from the same ``build_parser()`` that the CLI uses,
so it stays in sync automatically -- run this after editing the argument
definitions and commit the regenerated ``man/lbctl.1``.

Usage:
    python3 scripts/manpage.py            # write man/lbctl.1
    python3 scripts/manpage.py --check    # exit non-zero if the page is stale
"""
import argparse
import os
import sys
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LBCTL_PATH = os.path.join(ROOT, "lbctl")
MANPAGE_PATH = os.path.join(ROOT, "man", "lbctl.1")
PROG = "lbctl"
SECTION = "1"
WIDTH = 72


def load_module():
    # lbctl has no .py extension; load it like the tests do, without
    # executing main() or touching the network.
    return SourceFileLoader("lbctl", LBCTL_PATH).load_module()


def roff_wrap(text, width=WIDTH):
    words = text.split()
    line = ""
    for word in words:
        if not line:
            line = word
        elif len(line) + 1 + len(word) <= width:
            line += " " + word
        else:
            yield line
            line = word
    if line:
        yield line


def roff_escape(text):
    """Escape the few characters that would otherwise be roff commands.

    Backslashes and double quotes need care: a backslash introduces roff
    escapes and a leading '.' on a line would be read as a request. Quotes
    are paired into the roff quote escapes so straight text round-trips.
    """
    text = text.replace("\\", "\\\\")
    out, i, n = "", 0, len(text)
    open_quote = True
    while i < n:
        ch = text[i]
        if ch == '"':
            out += "\\(dq" if open_quote else "\\(sd"
            open_quote = not open_quote
        else:
            out += ch
        i += 1
    return out


def emit_safe(lines, out):
    # A roff line beginning with '.' or '-' would be parsed as a request;
    # backslash-escape the leading marker so it prints literally.
    for line in lines:
        if line and line[0] in ".-":
            out.append("\\&" + line)
        else:
            out.append(line)


def option_line(parser_action):
    strings = parser_action.option_strings
    # Skip the auto-added help flag; it appears on every parser.
    if strings and strings[0] in ("-h", "--help"):
        return None
    flags = ", ".join(strings)
    metavar = parser_action.metavar
    if isinstance(metavar, (list, tuple)):
        metavar = metavar[0] if metavar else ""
    if metavar:
        return f".B {flags}, {metavar}"
    return f".B {flags}"


def main(argv):
    check = "--check" in argv
    mod = load_module()
    parser = mod.build_parser()

    # Top-level options and the set of metavars they define, so per-command
    # options that merely inherit the connection flags are not repeated.
    global_metavars = set()
    for action in parser._actions:
        if action.metavar and action.metavar is not argparse.SUPPRESS \
                and action.option_strings:
            global_metavars.add(action.metavar)

    lines = []
    version = getattr(mod, "__version__", "unknown")

    # -- NAME / SYNOPSIS ---------------------------------------------------
    lines.append(f".TH {PROG.upper()} {SECTION} \"{PROG} {version}\" "
                 "\"Sep 2026\" \"User Commands\"")
    lines.append("")
    summary = mod.__doc__.split("\n")[0]
    lines.append(".SH NAME")
    emit_safe([f"{PROG} \\- {roff_escape(summary)}"], lines)
    lines.append("")
    lines.append(".SYNOPSIS")
    lines.append(f".B {PROG}")
    lines.append(".I [global options]")
    lines.append(".IC command")
    lines.append("")

    # -- DESCRIPTION -------------------------------------------------------
    lines.append(".SH DESCRIPTION")
    blocks = mod.__doc__.split("\n\n")
    for block in blocks:
        block = block.strip("\n ")
        if not block:
            continue
        lines.append(".PP")
        emit_safe(roff_wrap(roff_escape(block)), lines)
    lines.append("")

    # -- GLOBAL OPTIONS ----------------------------------------------------
    lines.append(".SH GLOBAL OPTIONS")
    for action in parser._actions:
        if not action.option_strings:
            continue
        if {"-h", "--help"} <= set(action.option_strings):
            continue
        if action.metavar is argparse.SUPPRESS:
            continue
        line = option_line(action)
        if line is None:
            continue
        lines.append(line)
        help_text = getattr(action, "help", None) or ""
        if help_text:
            emit_safe(roff_wrap(roff_escape(help_text)), lines)
    lines.append("")

    # -- COMMANDS ----------------------------------------------------------
    # add_subparsers() records the subparsers action in _actions; find it by
    # class name rather than relying on a private attribute.
    sub_action = next(
        (a for a in parser._actions
         if a.__class__.__name__ == "_SubParsersAction"), None)
    choices = sub_action.choices if sub_action is not None else {}
    if choices:
        lines.append(".SH COMMANDS")
        for name, sub in choices.items():
            if sub.description is argparse.SUPPRESS:
                continue
            label = name
            aliases = getattr(sub, "aliases", None)
            if aliases:
                label += " (" + ", ".join(aliases) + ")"
            lines.append(".TP")
            lines.append(f".B {label}")
            desc = getattr(sub, "description", None) or ""
            if desc:
                emit_safe(roff_wrap(roff_escape(desc)), lines)
            for action in sub._actions:
                if not action.option_strings:
                    continue
                if {"-h", "--help"} <= set(action.option_strings):
                    continue
                if action.metavar in global_metavars:
                    continue
                if action.metavar is argparse.SUPPRESS:
                    continue
                line = option_line(action)
                if line is None:
                    continue
                lines.append(line)
                help_text = getattr(action, "help", None) or ""
                if help_text:
                    emit_safe(roff_wrap(roff_escape(help_text)), lines)
    lines.append("")

    # -- EXAMPLES ----------------------------------------------------------
    lines.append(".SH EXAMPLES")
    examples = [
        ("Check one member's state (name or IP works for --member):",
         f"{PROG} --pool web_pool --member web03.example.com status"),
        ("List every member in a pool so you can pick one to target:",
         f"{PROG} --pool web_pool list-members"),
        ("Disable a member and keep it (no prompt, no wait):",
         f"{PROG} --pool web_pool --member 10.1.2.3 --disable"),
        ("Bring a previously disabled member back into rotation:",
         f"{PROG} --pool web_pool --member web03.example.com --enable"),
    ]
    for intro, command in examples:
        lines.append(".PP")
        emit_safe(roff_wrap(roff_escape(intro + " ")), lines)
        lines.append(".RS")
        emit_safe([f".B {roff_escape(command)}"], lines)
        lines.append(".RE")
    lines.append("")

    # -- FILES -------------------------------------------------------------
    lines.append(".SH FILES")
    lines.append(".TP")
    lines.append(".I ~/.lbctl.toml")
    lines.append("Connection defaults (password included). The tool refuses "
                 "to load it unless it is mode 600.")
    lines.append("")

    # -- SEE ALSO ----------------------------------------------------------
    # `.BR` is a roff request, not prose, so it goes out directly; emit_safe
    # is meant only for wrapped prose lines.
    lines.append(".SH SEE ALSO")
    lines.append(".BR bash (1),")
    lines.append(".BR zsh (1)")
    lines.append("")

    # -- AUTHORS -----------------------------------------------------------
    lines.append(".SH AUTHORS")
    emit_safe(
        [f"{PROG} was written by Ryan Gelber "
          "<ryangelber@gmail.com>."], lines)
    lines.append("")

    page = "\n".join(lines) + "\n"
    if check:
        with open(MANPAGE_PATH, "r") as fh:
            current = fh.read()
        if current != page:
            print("man page is stale -- run: python3 scripts/manpage.py",
                  file=sys.stderr)
            return 1
        print("man page up to date", file=sys.stderr)
        return 0
    os.makedirs(os.path.dirname(MANPAGE_PATH), exist_ok=True)
    with open(MANPAGE_PATH, "w") as fh:
        fh.write(page)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))