"""Guided choice of official Domains and Extensions from the Platform catalogue.

The list comes from the Platform's catalogue.json, the same file the Software
page reads. Choosing changes nothing by itself: the caller runs ./bilibi add
for each confirmed choice, so add's checks and refusals stay the only way in.
"""
import contextlib
import os
import shutil
import sys

LAYERS = (('domains', 'Domains'), ('extensions', 'Extensions'))
HELP = ('Up/down arrows move, space ticks, or type an item\'s number. '
        'Enter goes on; q cancels.')


def options(catalogue, mounted=()):
    """Catalogue entries not yet added here, in catalogue order: (layer, id, name, description)."""
    found = []
    for layer, _ in LAYERS:
        for component, facts in (catalogue.get(layer) or {}).items():
            if f'apps/{layer}/{component}' in mounted:
                continue
            found.append((layer, component, facts.get('name') or component,
                          ' '.join(str(facts.get('description') or '').split())))
    return found


def interactive():
    """A person at a terminal: skip in CI and for agents, which have no terminal."""
    return sys.stdin.isatty() and sys.stdout.isatty() and not os.environ.get('CI')


def listing(choices, cursor=None, ticked=(), width=None):
    """The numbered list, one line per entry, cut to the terminal width."""
    width = width or shutil.get_terminal_size((100, 24)).columns
    lines = []
    for title_layer, title in LAYERS:
        group = [(number, entry) for number, entry in enumerate(choices, 1) if entry[0] == title_layer]
        if not group:
            continue
        lines.append(f'{title}:')
        for number, (_, component, name, description) in group:
            pointer = '>' if cursor == number - 1 else ' '
            box = '' if cursor is None else ('[x] ' if number - 1 in ticked else '[ ] ')
            line = f'{pointer} {box}{number:>2}. {name} ({component})'
            if description:
                line += f' - {description}'
            lines.append(line if len(line) <= width - 1 else line[:max(width - 2, 10)] + '…')
    return lines


def plain(choices):
    """What a script or agent sees: the list and the add commands, nothing changed."""
    print('\n'.join(listing(choices, width=10_000)))
    print('\nNo terminal, so nothing was added. Add one with ./bilibi add <id>, such as: '
          f'./bilibi add {choices[0][1]}')


@contextlib.contextmanager
def keys(stream):
    """Key names from a terminal, in cbreak mode from the moment the list is drawn.

    The mode is set before anything is shown, so keys typed early are read as
    keys rather than echoed, and the terminal is restored however choosing ends."""
    import termios
    import tty
    descriptor = stream.fileno()
    saved = termios.tcgetattr(descriptor)
    tty.setcbreak(descriptor)

    def read():
        while True:
            data = os.read(descriptor, 16).decode(errors='ignore')
            if not data:
                yield 'quit'
                return
            yield from decode(data)
    try:
        yield read()
    finally:
        termios.tcsetattr(descriptor, termios.TCSADRAIN, saved)


def decode(data):
    """Turn bytes read from a terminal into key names; several keys can arrive at once."""
    while data:
        for sequence, name in (('\x1b[A', 'up'), ('\x1bOA', 'up'), ('\x1b[B', 'down'), ('\x1bOB', 'down')):
            if data.startswith(sequence):
                yield name
                data = data[len(sequence):]
                break
        else:
            key, data = data[0], data[1:]
            if key == '\x1b':
                # Escape alone cancels; an unknown escape sequence is ignored.
                if not data:
                    yield 'quit'
                data = ''
            elif key in '\r\n':
                yield 'enter'
            elif key == ' ':
                yield 'space'
            elif key in 'qQ\x03\x04':
                yield 'quit'
            elif key in 'kK':
                yield 'up'
            elif key in 'jJ':
                yield 'down'
            else:
                yield key


def pick(choices, read, out):
    """Let the person tick entries; return the ticked ones, or None when cancelled.

    read yields key names from keys(); out is the terminal to draw on."""
    cursor, ticked, number, drawn = 0, set(), '', 0

    def draw():
        nonlocal drawn
        if drawn:
            out.write(f'\x1b[{drawn}F\x1b[J')
        lines = listing(choices, cursor, ticked) + [HELP if not number else f'Number: {number}']
        out.write('\n'.join(lines) + '\n')
        out.flush()
        drawn = len(lines)

    def toggle(index):
        nonlocal cursor
        if 0 <= index < len(choices):
            cursor = index
            ticked.symmetric_difference_update({index})

    draw()
    for key in read:
        if key.isdigit():
            number += key
            # Toggle at once unless another digit could still name an entry.
            if int(number + '0') > len(choices):
                toggle(int(number) - 1)
                number = ''
        else:
            if number:
                toggle(int(number) - 1)
                number = ''
                if key in ('space', 'enter'):
                    draw()
                    continue
            if key == 'quit':
                return None
            if key == 'up':
                cursor = (cursor - 1) % len(choices)
            elif key == 'down':
                cursor = (cursor + 1) % len(choices)
            elif key == 'space':
                toggle(cursor)
            elif key == 'enter':
                return [choices[index] for index in sorted(ticked)]
        draw()
    return None


def confirm(chosen, read, out):
    names = ', '.join(f'{name} ({component})' for _, component, name, _ in chosen)
    out.write(f'Add {names}? Press y to add, any other key to cancel.\n')
    out.flush()
    return next(iter(read), 'quit') in ('y', 'Y')


def guided(catalogue, mounted, add, read=None, out=None):
    """Show the list and run add for each confirmed choice; return the added ids.

    Without a terminal it only prints the list. Nothing is added until the
    person confirms; add raises on the first refusal, keeping earlier ones."""
    choices = options(catalogue, mounted)
    if not choices:
        print('Every official Domain and Extension in the catalogue is already added. Run ./bilibi status.')
        return []
    if read is None and not interactive():
        plain(choices)
        return []
    out = out or sys.stdout
    with (keys(sys.stdin) if read is None else contextlib.nullcontext(iter(read))) as read:
        out.write('Choose the business features to add. A Domain is one business area, such as Finance; '
                  'an Extension adapts or connects one.\n')
        try:
            chosen = pick(choices, read, out)
            if chosen and not confirm(chosen, read, out):
                chosen = None
        except KeyboardInterrupt:
            chosen = None
    if chosen is None:
        print('Cancelled; nothing was added. Run ./bilibi choose to see the list again.', flush=True)
        return []
    if not chosen:
        print('Nothing ticked; nothing was added. Run ./bilibi choose to see the list again.', flush=True)
        return []
    added = []
    for _, component, name, _ in chosen:
        try:
            add(component)
        except Exception:
            if added:
                print(f'Added before the problem below: {", ".join(added)}. They stay in place.', file=sys.stderr)
            raise
        added.append(component)
    return added
