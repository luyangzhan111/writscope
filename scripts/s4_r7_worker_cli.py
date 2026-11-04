"""Offline-only worker candidate CLI: prepare, run, evaluate. No live option."""
import argparse
import json
from pathlib import Path

from scripts.s4_r5_acceptance import POLICY
from scripts.s4_r7_worker_runtime import prepare, run, evaluate, ROOT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('prepare', 'run', 'evaluate'))
    parser.add_argument('--bundle-root', type=Path, required=True)
    parser.add_argument('--scripts', type=Path, required=True, help='Legacy compatibility hash only; never executes scripted methods')
    parser.add_argument('--fixtures', type=Path, help='Fixed offline provider only')
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--run', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--cases', nargs='+', default=['s4_027'])
    parser.add_argument('--repeats', type=int, default=1)
    parser.add_argument('--deadline', type=float, default=90)
    args = parser.parse_args()
    if args.command == 'run':
        if not all((args.plan, args.run, args.fixtures)):
            parser.error('run requires --plan, --run, --fixtures')
        run(json.loads(args.plan.read_text(encoding='utf-8')), args.run, args.bundle_root, args.scripts, args.fixtures)
        return
    if not args.output:
        parser.error('prepare/evaluate requires --output')
    excluded = [args.bundle_root, ROOT/'evaluation_private'] + ([args.run] if args.run else [])
    if any(args.output.resolve().is_relative_to(p.resolve()) for p in excluded) or args.output.resolve() == args.scripts.resolve():
        parser.error('output overlaps bound evidence or references')
    if args.command == 'prepare':
        if not args.fixtures:
            parser.error('prepare requires --fixtures')
        result = prepare(args.bundle_root, args.scripts, args.fixtures, args.cases, args.repeats, POLICY, deadline=args.deadline)
    else:
        if not args.run:
            parser.error('evaluate requires --run')
        result = evaluate(args.run, args.bundle_root, args.scripts)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)


if __name__ == '__main__':
    main()
