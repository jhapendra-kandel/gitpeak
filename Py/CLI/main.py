#!/usr/bin/env python3
"""
  ____ _ _   ____           _    
 / ___(_) |_|  _ \\ ___  ___| | __
| |  _| | __| |_) / _ \\/ _ \\ |/ /
| |_| | | |_|  __/  __/  __/   < 
 \\____|_|\\__|_|   \\___|\\___|\\_|\\_\\

GitPeek — GitHub Repository Explorer (CLI)
Headless-friendly · Works on any terminal
"""

import sys
import os
import json
import re
import time
import argparse
import threading
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.parse import quote
from urllib.error import HTTPError, URLError

# ─── Optional rich ────────────────────────────────────────────────────────────
try:
    from rich.console import Console
    from rich.tree import Tree as RichTree
    from rich.syntax import Syntax
    from rich.panel import Panel
    from rich.table import Table
    from rich.progress import Progress, SpinnerColumn, TextColumn
    from rich.prompt import Prompt, Confirm
    from rich.columns import Columns
    from rich import print as rprint
    from rich.text import Text
    from rich.rule import Rule
    HAS_RICH = True
    console = Console()
except ImportError:
    HAS_RICH = False
    console = None

# ─── Optional requests ────────────────────────────────────────────────────────
try:
    import requests as req_lib
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

# ══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ══════════════════════════════════════════════════════════════════════════════
APP_NAME    = "GitPeek"
APP_VERSION = "1.0.0"
API_BASE    = "https://api.github.com"
RAW_BASE    = "https://raw.githubusercontent.com"
DATA_DIR    = Path.home() / ".gitpeek"
HISTORY_FILE = DATA_DIR / "cli_history.json"

DATA_DIR.mkdir(parents=True, exist_ok=True)

ASCII_LOGO = r"""
  ____ _ _   ____           _    
 / ___(_) |_|  _ \ ___  ___| | __
| |  _| | __| |_) / _ \/ _ \ |/ /
| |_| | | |_|  __/  __/  __/   < 
 \____|_|\__|_|   \___|\___|_|\_\
"""

MINI_LOGO = "  [bold cyan]Git[/bold cyan][bold white]Peek[/bold white] [dim]v{v}[/dim]".format(v=APP_VERSION)

TEXT_EXTS = {
    'js','jsx','ts','tsx','mjs','cjs','css','scss','sass','less',
    'html','htm','xml','svg','json','jsonc','yaml','yml','toml','ini',
    'env','md','mdx','txt','log','csv','py','rb','go','rs','java',
    'kt','swift','c','cpp','h','hpp','cs','php','pl','lua','r','sh',
    'bash','zsh','fish','ps1','vue','svelte','elm','ex','exs','erl',
    'hs','dart','scala','groovy','makefile','dockerfile','gitignore',
    'editorconfig','prisma','graphql','gql','proto','conf','cfg',
}
IMAGE_EXTS = {'png','jpg','jpeg','gif','webp','ico','bmp','avif','svg'}

LANG_MAP = {
    'py':'python','js':'javascript','ts':'typescript','jsx':'jsx',
    'tsx':'tsx','css':'css','scss':'scss','html':'html','xml':'xml',
    'json':'json','yaml':'yaml','yml':'yaml','toml':'toml','md':'markdown',
    'sh':'bash','bash':'bash','go':'go','rs':'rust','java':'java',
    'rb':'ruby','c':'c','cpp':'cpp','cs':'csharp','php':'php',
    'lua':'lua','r':'r','vue':'html','svelte':'html','dockerfile':'docker',
    'graphql':'graphql','proto':'protobuf',
}

FILE_ICONS = {
    'py':'🐍','js':'🟨','ts':'🔷','jsx':'⚛','tsx':'⚛',
    'html':'🌐','css':'🎨','scss':'🎨','json':'📋','yaml':'⚙',
    'yml':'⚙','toml':'⚙','md':'📝','sh':'⚙','bash':'⚙',
    'go':'🐹','rs':'⚙','java':'☕','rb':'💎','php':'🐘',
    'vue':'💚','svelte':'🧡','dockerfile':'🐳','txt':'📄',
    'png':'🖼','jpg':'🖼','jpeg':'🖼','gif':'🖼','svg':'🖼',
    'mp4':'🎬','pdf':'📄','zip':'🗜','tar':'🗜',
}

DIR_ICON  = '📁'
FILE_ICON = '📄'


# ══════════════════════════════════════════════════════════════════════════════
# OUTPUT HELPERS (works with and without rich)
# ══════════════════════════════════════════════════════════════════════════════
def out(text='', markup=True, **kwargs):
    if HAS_RICH:
        if markup:
            console.print(text, **kwargs)
        else:
            console.print(text, markup=False, **kwargs)
    else:
        # Strip rich markup
        clean = re.sub(r'\[/?[^\]]+\]', '', str(text))
        print(clean, **{k:v for k,v in kwargs.items() if k in ('end','file')})

def out_rule(title=''):
    if HAS_RICH:
        console.print(Rule(title, style='dim'))
    else:
        w = os.get_terminal_size().columns if hasattr(os, 'get_terminal_size') else 60
        print(f'── {title} ' + '─' * max(0, w - len(title) - 4))

def err(text):
    if HAS_RICH:
        console.print(f'[bold red]✗[/bold red] {text}')
    else:
        print(f'ERROR: {text}', file=sys.stderr)

def ok(text):
    if HAS_RICH:
        console.print(f'[bold green]✓[/bold green] {text}')
    else:
        print(f'OK: {text}')

def info(text):
    if HAS_RICH:
        console.print(f'[dim cyan]ℹ[/dim cyan] [dim]{text}[/dim]')
    else:
        print(f'  {text}')

def spin(message):
    """Return a progress spinner context."""
    if HAS_RICH:
        return Progress(SpinnerColumn(), TextColumn(message), transient=True)
    return None


# ══════════════════════════════════════════════════════════════════════════════
# API HELPERS
# ══════════════════════════════════════════════════════════════════════════════
def api_get(path, token=None):
    url = path if path.startswith('http') else f'{API_BASE}{path}'
    headers = {'Accept': 'application/vnd.github.v3+json',
               'User-Agent': f'GitPeek-CLI/{APP_VERSION}'}
    if token:
        headers['Authorization'] = f'token {token}'
    if HAS_REQUESTS:
        resp = req_lib.get(url, headers=headers, timeout=15)
        if resp.status_code == 404:
            raise FileNotFoundError(f'Not found: {url}')
        if resp.status_code == 403:
            raise PermissionError('GitHub API rate limit exceeded.')
        resp.raise_for_status()
        rem = resp.headers.get('X-RateLimit-Remaining')
        if rem and int(rem) < 5:
            out(f'[yellow]⚠ API rate limit low: {rem} remaining[/yellow]')
        return resp.json()
    else:
        rq = Request(url, headers=headers)
        with urlopen(rq, timeout=15) as r:
            return json.loads(r.read().decode())

def fetch_raw_text(owner, name, branch, path, token=None):
    url = f'{RAW_BASE}/{owner}/{name}/{branch}/{quote(path, safe="/")}'
    headers = {'User-Agent': f'GitPeek-CLI/{APP_VERSION}'}
    if token:
        headers['Authorization'] = f'token {token}'
    if HAS_REQUESTS:
        resp = req_lib.get(url, headers=headers, timeout=20)
        resp.raise_for_status()
        return resp.text
    else:
        rq = Request(url, headers=headers)
        with urlopen(rq, timeout=20) as r:
            return r.read().decode('utf-8', errors='replace')


# ══════════════════════════════════════════════════════════════════════════════
# PARSING
# ══════════════════════════════════════════════════════════════════════════════
def parse_repo_input(raw):
    raw = raw.strip()
    m = re.search(r'github\.com/([^/\s]+)/([^/\s#?]+)', raw, re.I)
    if m:
        return m.group(1), re.sub(r'\.git$', '', m.group(2))
    m = re.match(r'^([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)$', raw)
    if m:
        return m.group(1), m.group(2)
    return None, None

def get_ext(filename):
    base = filename.split('/')[-1]
    dot  = base.rfind('.')
    return base[dot+1:].lower() if dot > 0 else base.lower()

def get_icon(name, is_dir=False):
    if is_dir:
        return DIR_ICON
    ext = get_ext(name)
    return FILE_ICONS.get(ext, FILE_ICON)


# ══════════════════════════════════════════════════════════════════════════════
# TREE BUILDING
# ══════════════════════════════════════════════════════════════════════════════
def build_tree_structure(items):
    root = {}
    for item in items:
        parts = item['path'].split('/')
        node  = root
        for i, part in enumerate(parts):
            if part not in node:
                node[part] = {'__children__': {}, '__item__': None}
            if i == len(parts) - 1:
                node[part]['__item__'] = item
            node = node[part]['__children__']
    return root

def list_dir(structure, path=''):
    """Return items in a directory path."""
    node = structure
    if path:
        for part in path.split('/'):
            if part in node:
                node = node[part]['__children__']
            else:
                return None
    return node

def render_tree_rich(structure, path='', max_depth=3, current_depth=0, prefix=''):
    """Render tree as Rich markup lines."""
    if current_depth >= max_depth:
        return ['[dim]  … (use ls <path> to explore deeper)[/dim]']
    node = list_dir(structure, path) or structure
    lines = []
    items = sorted(node.items(), key=lambda x: (
        0 if (not x[1]['__item__'] or x[1]['__item__']['type'] == 'tree') else 1,
        x[0].lower()
    ))
    for i, (name, val) in enumerate(items):
        is_last = (i == len(items) - 1)
        item = val.get('__item__')
        is_dir = not item or item.get('type') == 'tree'
        connector = '└─' if is_last else '├─'
        icon = get_icon(name, is_dir)
        ext  = get_ext(name)
        color = {
            'py':'yellow','js':'yellow','ts':'cyan','tsx':'cyan','jsx':'cyan',
            'css':'blue','scss':'magenta','html':'green','json':'bright_yellow',
            'md':'bright_white','sh':'bright_green','go':'cyan','rs':'red',
            'java':'red','py':'yellow',
        }.get(ext, 'white')
        style = 'bold' if is_dir else ''
        line  = f'{prefix}{connector} {icon} [{style}{color}]{name}[/{style}{color}]'
        if is_dir:
            line += '[dim]/[/dim]'
        size = item.get('size', 0) if item and not is_dir else 0
        if size > 0:
            line += f' [dim]{_fmt_size(size)}[/dim]'
        lines.append(line)
        if is_dir and val['__children__']:
            child_prefix = prefix + ('   ' if is_last else '│  ')
            lines.extend(render_tree_rich(val['__children__'], '',
                                           max_depth, current_depth + 1,
                                           child_prefix))
    return lines

def _fmt_size(b):
    if b < 1024: return f'{b}B'
    if b < 1048576: return f'{b/1024:.1f}KB'
    return f'{b/1048576:.1f}MB'

def render_tree_plain(structure, path='', max_depth=3, depth=0, prefix=''):
    if depth >= max_depth:
        return ['  … (deeper levels hidden)']
    node = list_dir(structure, path) or structure
    lines = []
    items = sorted(node.items(), key=lambda x: (
        0 if (not x[1]['__item__'] or x[1]['__item__']['type'] == 'tree') else 1,
        x[0].lower()
    ))
    for i, (name, val) in enumerate(items):
        is_last = (i == len(items) - 1)
        item = val.get('__item__')
        is_dir = not item or item.get('type') == 'tree'
        connector = '└─' if is_last else '├─'
        suffix = '/' if is_dir else ''
        lines.append(f'{prefix}{connector} {name}{suffix}')
        if is_dir and val['__children__']:
            child_prefix = prefix + ('   ' if is_last else '│  ')
            lines.extend(render_tree_plain(val['__children__'], '',
                                            max_depth, depth + 1, child_prefix))
    return lines


# ══════════════════════════════════════════════════════════════════════════════
# MAIN CLI CLASS
# ══════════════════════════════════════════════════════════════════════════════
class GitPeekCLI:
    def __init__(self):
        self.repo     = None  # {'owner':…,'name':…,'branch':…}
        self.tree     = []
        self.tree_map = {}
        self.structure = {}
        self.file_index = []
        self.token    = os.environ.get('GITHUB_TOKEN', '')
        self.cwd      = ''    # current virtual directory
        self.history  = load_history()

    # ─── LOAD REPO ────────────────────────────────────────────────────────────
    def cmd_load(self, args):
        if not args:
            err('Usage: load <owner/repo>  or  load <github URL>')
            return
        owner, name = parse_repo_input(' '.join(args))
        if not owner:
            err('Invalid repo. Use: owner/name  or  https://github.com/owner/name')
            return
        out(f'[dim]Fetching[/dim] [bold]{owner}/{name}[/bold][dim]…[/dim]')
        try:
            meta   = api_get(f'/repos/{owner}/{name}', self.token)
            branch = meta.get('default_branch', 'main')
            self.repo = {'owner': owner, 'name': name, 'branch': branch,
                          'full': f'{owner}/{name}', 'meta': meta}

            out(f'[dim]Loading file tree for branch[/dim] [cyan]{branch}[/cyan][dim]…[/dim]')
            tree_data = api_get(
                f'/repos/{owner}/{name}/git/trees/{branch}?recursive=1', self.token)
            items = tree_data.get('tree', [])
            self.tree      = items
            self.tree_map  = {i['path']: i for i in items}
            self.structure = build_tree_structure(items)
            self.file_index = [i['path'] for i in items if i['type'] == 'blob']
            self.cwd       = ''

            add_to_history(self.history, owner, name, branch)
            save_history(self.history)

            fc = len(self.file_index)
            dc = len([i for i in items if i['type'] == 'tree'])
            out()
            if HAS_RICH:
                tbl = Table.grid(padding=(0, 2))
                tbl.add_column(style='dim')
                tbl.add_column(style='bold white')
                tbl.add_row('Repo:',    f'{owner}/{name}')
                tbl.add_row('Branch:',  branch)
                tbl.add_row('Files:',   str(fc))
                tbl.add_row('Folders:', str(dc))
                tbl.add_row('Stars:',   str(meta.get('stargazers_count', 0)))
                tbl.add_row('Lang:',    meta.get('language') or '—')
                desc = meta.get('description') or ''
                if desc:
                    tbl.add_row('Desc:', desc[:70] + ('…' if len(desc)>70 else ''))
                console.print(Panel(tbl, title=f'[bold cyan]{owner}/{name}[/bold cyan]',
                                    border_style='dim', expand=False))
            else:
                print(f'\nLoaded: {owner}/{name}  |  {fc} files  |  branch: {branch}')
            out()
            ok(f'Loaded [bold]{owner}/{name}[/bold]  [{fc} files · {dc} dirs]')
        except FileNotFoundError:
            err(f'Repository not found: {owner}/{name}')
        except PermissionError as e:
            err(str(e))
        except Exception as e:
            err(f'Failed to load repo: {e}')

    # ─── LIST DIRECTORY ───────────────────────────────────────────────────────
    def cmd_ls(self, args):
        if not self.repo:
            err('No repo loaded. Use: load owner/repo')
            return
        path = args[0] if args else self.cwd
        # Normalize
        if path == '.':
            path = self.cwd
        elif not path.startswith('/') and self.cwd:
            path = f'{self.cwd}/{path}'.lstrip('/')

        node = list_dir(self.structure, path)
        if node is None:
            err(f'Path not found: {path}')
            return

        display = path or '/'
        out()
        out(f'[bold white]{self.repo["full"]}[/bold white] [dim]›[/dim] [cyan]{display}[/cyan]')
        out()

        items = sorted(node.items(), key=lambda x: (
            0 if (not x[1]['__item__'] or x[1]['__item__']['type'] == 'tree') else 1,
            x[0].lower()
        ))
        if not items:
            info('Empty directory')
            return

        if HAS_RICH:
            tbl = Table(show_header=False, box=None, padding=(0, 1))
            tbl.add_column(width=3)
            tbl.add_column()
            tbl.add_column(style='dim', justify='right')
            for name, val in items:
                item  = val.get('__item__')
                is_dir = not item or item.get('type') == 'tree'
                icon  = get_icon(name, is_dir)
                ext   = get_ext(name)
                color = 'yellow' if is_dir else {
                    'py':'bright_yellow','js':'yellow','ts':'cyan',
                    'html':'green','css':'blue','md':'bright_white',
                    'json':'bright_yellow','sh':'bright_green',
                }.get(ext, 'white')
                label = f'[{color}]{name}{"/" if is_dir else ""}[/{color}]'
                size  = item.get('size',0) if item and not is_dir else 0
                size_s = _fmt_size(size) if size > 0 else ''
                tbl.add_row(icon, label, size_s)
            console.print(tbl)
        else:
            for name, val in items:
                item  = val.get('__item__')
                is_dir = not item or item.get('type') == 'tree'
                print(f'  {"[DIR]" if is_dir else "     "}  {name}{"/" if is_dir else ""}')

        out()
        dirs  = sum(1 for _,v in items if not v['__item__'] or
                    v['__item__'].get('type') == 'tree')
        files = len(items) - dirs
        info(f'{dirs} directories · {files} files')

    # ─── TREE VIEW ────────────────────────────────────────────────────────────
    def cmd_tree(self, args):
        if not self.repo:
            err('No repo loaded. Use: load owner/repo')
            return
        depth = 3
        path  = ''
        for arg in args:
            if arg.isdigit():
                depth = int(arg)
            else:
                path = arg

        lines = (render_tree_rich if HAS_RICH else render_tree_plain)(
            self.structure, path, depth)

        display = path or self.repo['full']
        out()
        out(f'[bold cyan]{display}[/bold cyan]')
        for line in lines:
            out(line)
        out()
        info(f'Showing up to depth {depth}. Use tree <path> <depth> for more.')

    # ─── CHANGE DIRECTORY ─────────────────────────────────────────────────────
    def cmd_cd(self, args):
        if not self.repo:
            err('No repo loaded.')
            return
        if not args or args[0] in ('/', '~', ''):
            self.cwd = ''
            ok(f'Changed to root')
            return
        target = args[0].rstrip('/')
        if target == '..':
            if '/' in self.cwd:
                self.cwd = self.cwd.rsplit('/', 1)[0]
            else:
                self.cwd = ''
            ok(f'cwd: [cyan]/{self.cwd or ""}[/cyan]')
            return
        new_path = f'{self.cwd}/{target}'.lstrip('/') if self.cwd else target
        if new_path in self.tree_map and self.tree_map[new_path].get('type') == 'tree':
            self.cwd = new_path
            ok(f'cwd: [cyan]/{self.cwd}[/cyan]')
        elif list_dir(self.structure, new_path) is not None:
            self.cwd = new_path
            ok(f'cwd: [cyan]/{self.cwd}[/cyan]')
        else:
            err(f'Directory not found: {new_path}')

    # ─── CAT / VIEW FILE ──────────────────────────────────────────────────────
    def cmd_cat(self, args, lines_limit=None):
        if not self.repo:
            err('No repo loaded.')
            return
        if not args:
            err('Usage: cat <file path>')
            return
        path = args[0]
        # Resolve relative to cwd
        if not path.startswith('/') and self.cwd:
            path = f'{self.cwd}/{path}'.lstrip('/')
        path = path.lstrip('/')

        if path not in self.tree_map:
            # Try finding by filename in current dir
            matches = [p for p in self.file_index if p.endswith('/' + path) or p == path]
            if len(matches) == 1:
                path = matches[0]
            elif len(matches) > 1:
                out('Multiple matches:')
                for m in matches[:10]:
                    out(f'  {m}')
                return
            else:
                err(f'File not found: {path}')
                return

        item = self.tree_map[path]
        if item.get('type') == 'tree':
            err(f'{path} is a directory. Use: ls {path}')
            return

        ext    = get_ext(path)
        size   = item.get('size', 0)
        name   = path.split('/')[-1]

        if ext in IMAGE_EXTS:
            out()
            if HAS_RICH:
                console.print(Panel(
                    f'[dim]Image file — cannot display in terminal[/dim]\n'
                    f'URL: [link]{RAW_BASE}/{self.repo["owner"]}/{self.repo["name"]}/'
                    f'{self.repo["branch"]}/{path}[/link]',
                    title=f'[bold]{name}[/bold]'))
            else:
                r = self.repo
                print(f'\nImage: {name}')
                print(f'URL: {RAW_BASE}/{r["owner"]}/{r["name"]}/{r["branch"]}/{path}')
            return

        if ext not in TEXT_EXTS and size > 500000:
            out()
            err(f'File is binary or too large ({_fmt_size(size)}). Use: download {path}')
            return

        out(f'[dim]Fetching[/dim] [bold]{name}[/bold][dim]…[/dim]')
        try:
            r = self.repo
            text = fetch_raw_text(r['owner'], r['name'], r['branch'], path, self.token)
        except Exception as e:
            err(f'Could not fetch: {e}')
            return

        if lines_limit:
            lines = text.split('\n')[:lines_limit]
            text  = '\n'.join(lines)
            out(f'[dim](showing first {lines_limit} lines)[/dim]')

        out()
        if HAS_RICH:
            lang = LANG_MAP.get(ext, 'text')
            try:
                syn = Syntax(text, lang, theme='monokai', line_numbers=True,
                              word_wrap=False)
                title_text = f'{name}  [{ext.upper()}]  {_fmt_size(size)}'
                console.print(Panel(syn, title=title_text, border_style='dim'))
            except Exception:
                console.print(text)
        else:
            total = text.count('\n') + 1
            print(f'─── {name} ({_fmt_size(size)} · {total} lines) ─────────')
            for i, line in enumerate(text.split('\n'), 1):
                print(f'{i:4d}  {line}')
            print('─' * 40)

    # ─── HEAD (first N lines) ─────────────────────────────────────────────────
    def cmd_head(self, args):
        n = 20
        file_args = []
        for a in args:
            if a.lstrip('-').isdigit():
                n = int(a.lstrip('-'))
            else:
                file_args.append(a)
        self.cmd_cat(file_args, lines_limit=n)

    # ─── SEARCH FILES ─────────────────────────────────────────────────────────
    def cmd_search(self, args):
        if not self.repo:
            err('No repo loaded.')
            return
        if not args:
            err('Usage: search <query>')
            return
        query = ' '.join(args).lower()
        matches = [i for i in self.tree if i['type'] == 'blob' and
                   query in i['path'].lower()]

        out()
        out(f'[dim]Search:[/dim] [bold]{query}[/bold]')
        if not matches:
            out(f'[yellow]No files match "{query}"[/yellow]')
            return

        out(f'[dim]{len(matches)} result(s)[/dim]')
        out()

        if HAS_RICH:
            tbl = Table(show_header=True, header_style='bold dim',
                        box=None, padding=(0,1))
            tbl.add_column('#',    style='dim', width=4)
            tbl.add_column('File', style='bold white')
            tbl.add_column('Path', style='dim')
            tbl.add_column('Size', style='dim', justify='right')
            for idx, item in enumerate(matches[:50], 1):
                name = item['path'].split('/')[-1]
                path = item['path']
                dir_ = path[:path.rfind('/')] if '/' in path else ''
                ext  = get_ext(name)
                icon = get_icon(name)
                size = _fmt_size(item.get('size', 0))
                tbl.add_row(str(idx), f'{icon} {name}', dir_, size)
            console.print(tbl)
        else:
            for idx, item in enumerate(matches[:50], 1):
                print(f'  {idx:2d}. {item["path"]}')

        if len(matches) > 50:
            info(f'Showing 50 of {len(matches)} results.')

    # ─── FIND (grep-like) ─────────────────────────────────────────────────────
    def cmd_find(self, args):
        """Find files by extension or pattern."""
        if not self.repo:
            err('No repo loaded.')
            return
        if not args:
            err('Usage: find <ext|pattern>  e.g.  find py  or  find test')
            return
        pattern = args[0].lower().lstrip('.')
        matches = [i for i in self.tree if i['type'] == 'blob' and
                   (get_ext(i['path']) == pattern or
                    pattern in i['path'].lower())]
        out()
        if not matches:
            out(f'[yellow]No files found matching "{pattern}"[/yellow]')
            return
        out(f'[dim]{len(matches)} file(s) matching [bold]{pattern}[/bold][/dim]')
        out()
        for item in matches[:100]:
            name = item['path'].split('/')[-1]
            icon = get_icon(name)
            out(f'  {icon} {item["path"]} [dim]{_fmt_size(item.get("size",0))}[/dim]')

    # ─── DOWNLOAD FILE ────────────────────────────────────────────────────────
    def cmd_download(self, args):
        if not self.repo:
            err('No repo loaded.')
            return
        if not args:
            err('Usage: download <file path> [local name]')
            return
        path = args[0].lstrip('/')
        if self.cwd and not args[0].startswith('/'):
            path = f'{self.cwd}/{args[0]}'.lstrip('/')

        if path not in self.tree_map:
            matches = [p for p in self.file_index if p.endswith('/' + path) or p == path]
            if len(matches) == 1:
                path = matches[0]
            else:
                err(f'File not found: {path}')
                return

        local_name = args[1] if len(args) > 1 else path.split('/')[-1]
        r = self.repo
        url = f'{RAW_BASE}/{r["owner"]}/{r["name"]}/{r["branch"]}/{quote(path, safe="/")}'

        out(f'[dim]Downloading[/dim] [bold]{path}[/bold] [dim]→[/dim] {local_name}[dim]…[/dim]')
        try:
            if HAS_REQUESTS:
                resp = req_lib.get(url, timeout=60)
                resp.raise_for_status()
                data = resp.content
            else:
                with urlopen(Request(url), timeout=60) as r_:
                    data = r_.read()
            with open(local_name, 'wb') as f:
                f.write(data)
            ok(f'Saved [bold]{local_name}[/bold] [dim]({_fmt_size(len(data))})[/dim]')
        except Exception as e:
            err(f'Download failed: {e}')

    # ─── INFO ─────────────────────────────────────────────────────────────────
    def cmd_info(self, args):
        if not self.repo:
            err('No repo loaded.')
            return
        if args:
            # File info
            path = args[0].lstrip('/')
            if self.cwd:
                path = f'{self.cwd}/{path}'.lstrip('/')
            item = self.tree_map.get(path)
            if not item:
                err(f'Not found: {path}')
                return
            out()
            if HAS_RICH:
                tbl = Table.grid(padding=(0,2))
                tbl.add_column(style='dim')
                tbl.add_column()
                tbl.add_row('Path:', path)
                tbl.add_row('Type:', item.get('type','?'))
                tbl.add_row('Size:', _fmt_size(item.get('size',0)))
                tbl.add_row('SHA:',  item.get('sha','?')[:12] + '…')
                r = self.repo
                raw_url = f'{RAW_BASE}/{r["owner"]}/{r["name"]}/{r["branch"]}/{path}'
                tbl.add_row('Raw URL:', f'[link={raw_url}]{raw_url}[/link]')
                console.print(Panel(tbl, title=f'[bold]{path.split("/")[-1]}[/bold]'))
            else:
                print(f'\nFile: {path}')
                print(f'Size: {_fmt_size(item.get("size",0))}')
                print(f'SHA:  {item.get("sha","?")}')
        else:
            # Repo info
            meta = self.repo.get('meta', {})
            out()
            if HAS_RICH:
                tbl = Table.grid(padding=(0,2))
                tbl.add_column(style='dim')
                tbl.add_column()
                tbl.add_row('Name:',    self.repo['full'])
                tbl.add_row('Branch:',  self.repo['branch'])
                tbl.add_row('Files:',   str(len(self.file_index)))
                tbl.add_row('Stars:',   str(meta.get('stargazers_count','?')))
                tbl.add_row('Forks:',   str(meta.get('forks_count','?')))
                tbl.add_row('Language:', meta.get('language') or '—')
                tbl.add_row('License:', (meta.get('license') or {}).get('name','—'))
                tbl.add_row('Updated:', meta.get('updated_at','?')[:10])
                tbl.add_row('URL:', f'https://github.com/{self.repo["full"]}')
                desc = meta.get('description') or ''
                if desc:
                    tbl.add_row('Desc:', desc)
                console.print(Panel(tbl,
                    title=f'[bold cyan]{self.repo["full"]}[/bold cyan]',
                    border_style='dim'))
            else:
                print(f'\nRepo: {self.repo["full"]}')
                print(f'Branch: {self.repo["branch"]}')
                print(f'Files: {len(self.file_index)}')

    # ─── HISTORY ──────────────────────────────────────────────────────────────
    def cmd_history(self, args):
        if not self.history:
            info('No history yet.')
            return
        out()
        out('[bold]Recent repos:[/bold]')
        for i, entry in enumerate(self.history[:10], 1):
            out(f'  [dim]{i:2d}.[/dim] {entry.get("owner","?")}/{entry.get("name","?")} '
                 f'[dim]({entry.get("branch","?")}) '
                 f'{entry.get("time","")[:10]}[/dim]')

    # ─── OPEN IN BROWSER ──────────────────────────────────────────────────────
    def cmd_open(self, args):
        if not self.repo:
            err('No repo loaded.')
            return
        import webbrowser
        r = self.repo
        if args:
            path = args[0].lstrip('/')
            url  = f'https://github.com/{r["owner"]}/{r["name"]}/blob/{r["branch"]}/{path}'
        else:
            url = f'https://github.com/{r["full"]}'
        webbrowser.open(url)
        ok(f'Opened: {url}')

    # ─── STATS ────────────────────────────────────────────────────────────────
    def cmd_stats(self, args):
        if not self.repo:
            err('No repo loaded.')
            return
        exts = {}
        total_size = 0
        for item in self.tree:
            if item['type'] == 'blob':
                ext = get_ext(item['path'])
                exts[ext] = exts.get(ext, {'count': 0, 'size': 0})
                exts[ext]['count'] += 1
                exts[ext]['size']  += item.get('size', 0)
                total_size += item.get('size', 0)

        out()
        if HAS_RICH:
            tbl = Table(title='Repository Statistics', show_header=True,
                        header_style='bold dim', border_style='dim')
            tbl.add_column('Extension', style='bold')
            tbl.add_column('Files',     justify='right')
            tbl.add_column('Total Size', justify='right')
            tbl.add_column('Bar', min_width=20)

            top = sorted(exts.items(), key=lambda x: x[1]['count'], reverse=True)[:15]
            max_c = top[0][1]['count'] if top else 1
            for ext, data in top:
                bar_len = int(20 * data['count'] / max_c)
                bar  = '█' * bar_len + '░' * (20 - bar_len)
                tbl.add_row(
                    ext or '(none)',
                    str(data['count']),
                    _fmt_size(data['size']),
                    f'[dim]{bar}[/dim]'
                )
            console.print(tbl)
            console.print(f'\n[dim]Total files:[/dim] [bold]{len(self.file_index)}[/bold]  '
                           f'[dim]Total size:[/dim] [bold]{_fmt_size(total_size)}[/bold]')
        else:
            print('\nFile type statistics:')
            top = sorted(exts.items(), key=lambda x: x[1]['count'], reverse=True)[:15]
            for ext, data in top:
                print(f'  {ext or "(none)":12s}  {data["count"]:4d} files  {_fmt_size(data["size"]):8s}')
            print(f'\nTotal: {len(self.file_index)} files / {_fmt_size(total_size)}')

    # ─── HELP ─────────────────────────────────────────────────────────────────
    def cmd_help(self, args):
        out()
        if HAS_RICH:
            tbl = Table(show_header=False, box=None, padding=(0,2), expand=True)
            tbl.add_column(style='bold cyan', width=22)
            tbl.add_column(style='white')
            cmds = [
                ('load <owner/repo>',     'Load a GitHub repository'),
                ('ls [path]',             'List files in a directory'),
                ('cd <path>',             'Change virtual directory'),
                ('tree [path] [depth]',   'Show file tree'),
                ('cat <file>',            'View file contents with syntax highlighting'),
                ('head [-N] <file>',      'View first N lines of a file (default 20)'),
                ('search <query>',        'Search files by name'),
                ('find <ext|pattern>',    'Find files by extension or pattern'),
                ('download <file> [out]', 'Download a file'),
                ('info [file]',           'Show repo or file information'),
                ('stats',                 'Show file type statistics'),
                ('open [file]',           'Open repo or file on GitHub in browser'),
                ('history',               'Show recently loaded repos'),
                ('clear',                 'Clear the screen'),
                ('quit / exit',           'Exit GitPeek'),
                ('help',                  'Show this help message'),
            ]
            for cmd, desc in cmds:
                tbl.add_row(cmd, desc)
            console.print(Panel(tbl, title=MINI_LOGO, border_style='dim'))
        else:
            print('\nGitPeek CLI Commands:')
            print('  load <owner/repo>      Load a GitHub repository')
            print('  ls [path]              List files')
            print('  cd <path>              Change directory')
            print('  tree [path] [depth]    Show file tree')
            print('  cat <file>             View file contents')
            print('  head [-N] <file>       View first N lines')
            print('  search <query>         Search filenames')
            print('  find <pattern>         Find by extension')
            print('  download <file>        Download a file')
            print('  info [file]            Show info')
            print('  stats                  File type statistics')
            print('  open [file]            Open on GitHub.com')
            print('  history                Recent repos')
            print('  quit                   Exit')

    # ─── PROMPT ───────────────────────────────────────────────────────────────
    def _get_prompt(self):
        if not self.repo:
            return '[bold cyan]gitpeek[/bold cyan][dim]>[/dim] '
        cwd_display = f'/{self.cwd}' if self.cwd else ''
        return (f'[bold cyan]gitpeek[/bold cyan][dim]:[/dim]'
                f'[bold white]{self.repo["full"]}[/bold white]'
                f'[dim]{cwd_display}[/dim][dim]>[/dim] ')

    def _get_prompt_plain(self):
        if not self.repo:
            return 'gitpeek> '
        cwd_display = f'/{self.cwd}' if self.cwd else ''
        return f'gitpeek:{self.repo["full"]}{cwd_display}> '

    # ─── DISPATCH ─────────────────────────────────────────────────────────────
    def dispatch(self, line):
        line  = line.strip()
        if not line:
            return True
        parts = line.split()
        cmd   = parts[0].lower()
        args  = parts[1:]

        dispatch_map = {
            'load':     self.cmd_load,
            'ls':       self.cmd_ls,
            'list':     self.cmd_ls,
            'dir':      self.cmd_ls,
            'cd':       self.cmd_cd,
            'tree':     self.cmd_tree,
            'cat':      self.cmd_cat,
            'view':     self.cmd_cat,
            'head':     self.cmd_head,
            'search':   self.cmd_search,
            'find':     self.cmd_find,
            'download': self.cmd_download,
            'dl':       self.cmd_download,
            'info':     self.cmd_info,
            'stats':    self.cmd_stats,
            'open':     self.cmd_open,
            'history':  self.cmd_history,
            'help':     self.cmd_help,
            'clear':    lambda a: os.system('cls' if os.name=='nt' else 'clear'),
            'quit':     lambda a: False,
            'exit':     lambda a: False,
            'q':        lambda a: False,
        }

        handler = dispatch_map.get(cmd)
        if handler:
            result = handler(args)
            return result is not False
        else:
            err(f'Unknown command: {cmd}. Type "help" for commands.')
            return True

    # ─── INTERACTIVE REPL ─────────────────────────────────────────────────────
    def run_interactive(self):
        _print_logo()
        out()
        info('Type [bold]help[/bold] for commands. Type [bold]quit[/bold] to exit.')
        if self.token:
            ok('GitHub token detected. Rate limits increased.')
        else:
            info('No GITHUB_TOKEN env var set. Unauthenticated (60 req/hr).')
        out()

        while True:
            try:
                if HAS_RICH:
                    line = Prompt.ask(self._get_prompt_plain(), default='')
                else:
                    try:
                        line = input(self._get_prompt_plain())
                    except EOFError:
                        break
                if not self.dispatch(line):
                    break
            except KeyboardInterrupt:
                out()
                info('Use [bold]quit[/bold] to exit.')
            except Exception as e:
                err(f'Unexpected error: {e}')

        out()
        ok('Goodbye!')


# ══════════════════════════════════════════════════════════════════════════════
# HISTORY HELPERS
# ══════════════════════════════════════════════════════════════════════════════
def load_history():
    try:
        if HISTORY_FILE.exists():
            return json.loads(HISTORY_FILE.read_text('utf-8'))
    except Exception:
        pass
    return []

def save_history(history):
    try:
        HISTORY_FILE.write_text(json.dumps(history, indent=2), 'utf-8')
    except Exception:
        pass

def add_to_history(history, owner, name, branch):
    key  = f'{owner}/{name}'
    hist = [h for h in history if h.get('key') != key]
    hist.insert(0, {'key': key, 'owner': owner, 'name': name,
                     'branch': branch,
                     'time': time.strftime('%Y-%m-%dT%H:%M:%S')})
    history[:] = hist[:20]


# ══════════════════════════════════════════════════════════════════════════════
# LOGO
# ══════════════════════════════════════════════════════════════════════════════
def _print_logo():
    if HAS_RICH:
        console.print(f'[bold cyan]{ASCII_LOGO}[/bold cyan]')
        console.print(f'  [dim]GitHub Repository Explorer  v{APP_VERSION}[/dim]')
        console.print()
    else:
        print(ASCII_LOGO)
        print(f'  GitHub Repository Explorer  v{APP_VERSION}')
        print()


# ══════════════════════════════════════════════════════════════════════════════
# CLI ENTRY
# ══════════════════════════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(
        prog='gitpeek',
        description='GitPeek — GitHub Repository Explorer (CLI)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f'''
Examples:
  gitpeek                              # Interactive mode
  gitpeek load torvalds/linux          # Load repo and enter REPL
  gitpeek -r torvalds/linux tree 2     # Show tree depth 2 and exit
  gitpeek -r owner/repo cat README.md  # View README and exit

Environment:
  GITHUB_TOKEN    GitHub personal access token for higher rate limits
'''
    )
    parser.add_argument('-r', '--repo',
                        help='Repository to load (owner/repo or URL)',
                        metavar='OWNER/REPO')
    parser.add_argument('command', nargs='*',
                        help='Command to run (optional, enters REPL if omitted)')
    parser.add_argument('--version', action='version', version=f'{APP_NAME} {APP_VERSION}')
    parser.add_argument('--no-color', action='store_true',
                        help='Disable colored output')

    args = parser.parse_args()

    if args.no_color:
        global HAS_RICH
        HAS_RICH = False

    cli = GitPeekCLI()

    # Pre-load repo if specified
    if args.repo:
        cli.dispatch(f'load {args.repo}')

    # Run a one-off command if given
    if args.command:
        cmd_line = ' '.join(args.command)
        cli.dispatch(cmd_line)
        return

    # Interactive REPL
    cli.run_interactive()


if __name__ == '__main__':
    main()