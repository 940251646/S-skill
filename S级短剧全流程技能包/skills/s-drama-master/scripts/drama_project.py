#!/usr/bin/env python3
"""Optional standard-library tools for a drama project; no content quality scoring."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


TEMPLATE = Path(__file__).resolve().parents[1] / 'assets' / 'project-template.json'
EPISODE = re.compile(r'EP(\d{3,})\.md$')
SCENE = re.compile(r'^##\s+EP\d+-S\d+\b', re.M)
SPOKEN = re.compile(r'^(?:[^#【\[|：:]{1,40}(?:【[^】]*】|（[^）]*）)?|【(?:旁白|系统音)[^】]*】)[：:](.*)$')
META = ('目标时长', '当前时长', '出场', '目标', '场景', '人物', '实际时长', '总字数')


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(value, dict):
        raise ValueError(f'Expected an object: {path}')
    return value


def save_new(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8', newline='\n') as handle:
        handle.write(text)


def episode_files(project: Path) -> dict[int, Path]:
    result = {}
    for path in sorted((project / 'episodes').glob('EP*.md')):
        match = EPISODE.fullmatch(path.name)
        if not match:
            raise ValueError(f'Unexpected episode filename: {path.name}')
        number = int(match.group(1))
        if number < 1 or number in result:
            raise ValueError(f'Duplicate or invalid episode number: {number}')
        result[number] = path
    return result


def script_body(text: str) -> str:
    # Review notes are separated from the actual script by the shared template.
    return re.split(r'^---\s*$|^##\s+制作与审稿', text, maxsplit=1, flags=re.M)[0]


def measure(text: str, low_cpm: float = 180, high_cpm: float = 300) -> dict:
    body = script_body(text)
    utterances = []
    layers = {'dialogue': 0, 'inner_voice': 0, 'narration_system': 0}
    for raw in body.splitlines():
        line = raw.strip()
        if line.startswith(META):
            continue
        match = SPOKEN.match(line)
        if not match:
            continue
        words = re.sub(r'\s', '', match.group(1))
        utterances.append(words)
        kind = ('inner_voice' if '【内心' in line or '【心声' in line else
                'narration_system' if line.startswith(('【旁白', '【系统音')) else 'dialogue')
        layers[kind] += len(words)
    count = sum(map(len, utterances))
    return {
        'script_characters_with_punctuation': len(re.sub(r'\s', '', body)),
        'spoken_characters_with_punctuation': count,
        'spoken_layers': layers,
        'scene_count': len(SCENE.findall(body)),
        'estimated_voice_seconds': [round(count / high_cpm * 60, 1), round(count / low_cpm * 60, 1)],
        'timing_note': 'Voice-only estimate; add sequential action and pauses, and account for overlaps. Validate by read-through.',
        'has_episode_end': '【本集结束】' in body,
    }


def inspect(project: Path, low_cpm: float = 180, high_cpm: float = 300) -> dict:
    config = read_json(project / 'project.json')
    total = config.get('total_episodes')
    if not isinstance(total, int) or isinstance(total, bool) or total < 1:
        raise ValueError('Set total_episodes to a positive integer.')
    files = episode_files(project)
    expected = set(range(1, total + 1))
    rows = []
    for number, path in files.items():
        row = measure(path.read_text(encoding='utf-8-sig'), low_cpm, high_cpm)
        row.update(episode=number, file=path.name)
        rows.append(row)
    return {
        'project_name': config.get('project_name'),
        'planned_episodes': total,
        'episode_files': len(files),
        'missing_episodes': sorted(expected - set(files)),
        'extra_episodes': sorted(set(files) - expected),
        'format_incomplete': [r['episode'] for r in rows if not r['has_episode_end'] or r['scene_count'] == 0 or r['spoken_characters_with_punctuation'] == 0],
        'episodes': rows,
        'runtime_budget': runtime_report(config),
        'literary_review': 'Not assessed by this tool.',
    }


def runtime_report(config: dict) -> dict:
    total = config['total_episodes']
    budget = config.get('runtime_budget', {})
    opening = budget.get('opening_target_seconds', config.get('target_episode_seconds', 150))
    later = budget.get('later_target_seconds', config.get('target_episode_seconds', 65))
    allocation = config.get('episode_runtime_seconds')
    if allocation is None:
        allocation = [opening if i < 3 else later for i in range(total)]
    issues = []
    if not isinstance(allocation, list) or len(allocation) != total:
        return {'valid': False, 'issues': ['Runtime allocation length must match total_episodes.']}
    for number, seconds in enumerate(allocation, 1):
        prefix = 'opening' if number <= 3 else 'later'
        low, high = budget.get(prefix+'_min_seconds', 1), budget.get(prefix+'_max_seconds', float('inf'))
        if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or not low <= seconds <= high:
            issues.append(f'EP{number:03}: duration outside configured range {low}..{high}.')
    numeric = all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in allocation)
    seconds_total = sum(allocation) if numeric else None
    maximum = budget.get('max_total_seconds')
    if maximum is not None and seconds_total is not None and seconds_total > maximum:
        issues.append(f'Total runtime {seconds_total} exceeds budget {maximum}.')
    return {'valid': not issues, 'planned_seconds': seconds_total,
            'planned_minutes': round(seconds_total/60, 2) if seconds_total is not None else None,
            'maximum_seconds': maximum, 'issues': issues}


def initialize(output: Path, name: str, episodes: int | None, seconds: int | None) -> dict:
    if (output / 'project.json').exists() or (output / 'progress.json').exists():
        raise ValueError('Project configuration already exists; choose a new project directory.')
    config = read_json(TEMPLATE)
    config['project_name'] = name
    if episodes is not None:
        if episodes < 1:
            raise ValueError('episodes must be positive.')
        config['total_episodes'] = episodes
    if seconds is not None:
        if seconds < 1:
            raise ValueError('seconds must be positive.')
        config['target_episode_seconds'] = seconds
        config['runtime_override_note'] = 'Per-episode override; rebuild the season runtime allocation before writing.'
        config['episode_runtime_seconds'] = [seconds] * config['total_episodes']
    budget = config.get('runtime_budget', {})
    budget['default_total_seconds'] = min(3, config['total_episodes']) * budget.get('opening_target_seconds', 150) + max(0, config['total_episodes']-3) * budget.get('later_target_seconds', 65)
    progress = dict(project_name=name, planned_episodes=config['total_episodes'], canon_version=1,
                    stage='positioning', completed_episodes=[], reviewed_episodes=[], next_episode=1,
                    open_critical_issues=[], completion_status='in_progress')
    save_new(output / 'project.json', json.dumps(config, ensure_ascii=False, indent=2) + '\n')
    save_new(output / 'progress.json', json.dumps(progress, ensure_ascii=False, indent=2) + '\n')
    return {'project': str(output), 'created': ['project.json', 'progress.json']}


def assemble(project: Path, output: Path, overwrite: bool = False) -> dict:
    report = inspect(project)
    if report['missing_episodes'] or report['extra_episodes'] or report['format_incomplete']:
        raise ValueError('Assembly requires all expected episode files and complete script format. Run inspect for details.')
    if not report['runtime_budget']['valid']:
        raise ValueError('Runtime budget needs correction. Run inspect for details.')
    files = episode_files(project)
    protected = {p.resolve() for p in files.values()} | {(project / 'project.json').resolve(), (project / 'progress.json').resolve()}
    if output.resolve() in protected:
        raise ValueError('Output points to a project source file.')
    title = report['project_name'] or project.name
    combined = f'# {title}｜全剧文学剧本\n\n' + '\n\n---\n\n'.join(
        script_body(path.read_text(encoding='utf-8-sig')).strip() for _, path in sorted(files.items())) + '\n'
    if overwrite:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(combined, encoding='utf-8')
    else:
        save_new(output, combined)
    return {'output': str(output), 'episodes_assembled': len(files), 'literary_review': 'Requires separate evidence-based review.'}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    init = commands.add_parser('init', help='Create configuration without overwriting an existing project.')
    init.add_argument('--output', type=Path, required=True)
    init.add_argument('--name', required=True)
    init.add_argument('--episodes', type=int)
    init.add_argument('--seconds', type=int)
    check = commands.add_parser('inspect', help='List missing episodes and estimate all voice layers.')
    check.add_argument('--project', type=Path, required=True)
    check.add_argument('--low-cpm', type=float, default=180)
    check.add_argument('--high-cpm', type=float, default=300)
    merge = commands.add_parser('assemble', help='Merge all expected complete-format episode files.')
    merge.add_argument('--project', type=Path, required=True)
    merge.add_argument('--output', type=Path, required=True)
    merge.add_argument('--overwrite', action='store_true')
    args = parser.parse_args()
    try:
        if args.command == 'init':
            result = initialize(args.output, args.name, args.episodes, args.seconds)
        elif args.command == 'inspect':
            if not (0 < args.low_cpm <= args.high_cpm):
                raise ValueError('Require 0 < low-cpm <= high-cpm.')
            result = inspect(args.project, args.low_cpm, args.high_cpm)
        else:
            result = assemble(args.project, args.output, args.overwrite)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
