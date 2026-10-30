#!/usr/bin/env python3
"""Regenerate bundled credits from local history and GitHub's verified identities.

Requires Python 3.11+, git, gh, and a full checkout. No author names/emails are
written to the snapshot. Run before a release; builds remain entirely offline.
"""
import argparse
import collections
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()


def github(query):
    result = json.loads(subprocess.check_output(
        ['gh', 'api', 'graphql', '-f', f'query={query}'], text=True))
    if result.get('errors'):
        raise RuntimeError(result['errors'])
    return result['data']['repository']


def model_credits(message):
    """Only recognize model trailers from their provider's domain; deduplicate."""
    models = set()
    for name, email in re.findall(r'^Co-authored-by:\s*(.*?)\s*<([^>]+)>', message, re.I | re.M):
        domain = email.lower().rsplit('@', 1)[-1]
        if domain == 'anthropic.com':
            match = re.fullmatch(r'Claude(?: (Opus|Sonnet|Haiku|Fable))?(?: ([\d.]+))?(?: \(.*\))?', re.sub(r'^Claude Code$', 'Claude', name, flags=re.I), re.I)
            if match:
                family, version = match.groups()
                models.add(('Anthropic', 'Claude' + (f' {family.title()}' if family else ''), version or ''))
        elif domain == 'openai.com' and name.lower() in ('codex', 'openai codex'):
            models.add(('OpenAI', 'Codex', ''))
    # Older agent commits carry only a session trailer; retain those credits
    # without guessing a model family or version.
    if not any(company == 'Anthropic' for company, _, _ in models) and re.search(
            r'^Claude-Session: https://claude\.ai/code/\S+', message, re.M):
        models.add(('Anthropic', 'Claude', ''))
    return models


def utc(date):
    return datetime.fromisoformat(date).astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def blank(login):
    return dict(login=login, people_entry=False, github_name=None, prs=0, commits=0,
                lines_added=0, lines_deleted=0, binary_added=0, binary_deleted=0,
                first_commit='', last_commit='')


def verified_entries(people, identities):
    # Every non-comment line of an opt-in entry must be committed by that person.
    # Checking blame also handles entries that arrived together through a merge.
    text = git('blame', '--line-porcelain', 'HEAD', '--', 'contributors/people.toml')
    verified = {}
    sha = section = None
    for line in text.splitlines():
        if re.match(r'^[0-9a-f]{40} ', line):
            sha = line.split()[0]
        elif line.startswith('\t'):
            source = line[1:].strip()
            if source.startswith('[people.') and source.endswith(']'):
                section = source[8:-1]
            if source and not source.startswith('#') and section:
                owner = (identities.get(sha, {}).get('author') or {}).get('user') or {}
                verified[section] = verified.get(section, True) and owner.get('login', '').lower() == section.lower()
    return {login for login in people if verified.get(login, False)}


def human_logins(info):
    authors = info.get('authors', {}).get('nodes')
    if authors is None:
        authors = [dict(user=(info.get('author') or {}).get('user'))]
    return {a['user']['login'] for a in authors if a.get('user') and
            not model_credits('Co-Authored-By: ' + a.get('name', '') + ' <' + a.get('email', '') + '>')}


def refresh(repo, ref="HEAD"):
    if git('rev-parse', '--is-shallow-repository') != 'false':
        raise RuntimeError('A full checkout is required (git fetch --unshallow).')
    if git('status', '--porcelain', '--', 'contributors/people.toml'):
        raise RuntimeError('Commit people.toml first so consent can be verified.')
    owner, name = repo.split('/')
    head = git('rev-parse', ref)
    commits = git('rev-list', head).splitlines()
    reachable = set(commits)
    identities = {}
    prs = {}
    def resolve(batch):
        fields = ' '.join(f'c{i}: object(oid:"{sha}") {{ ... on Commit {{ author {{ user {{ login }} }} authors(first:100) {{ nodes {{ name email user {{ login }} }} pageInfo {{ hasNextPage }} }} associatedPullRequests(first:100) {{ nodes {{ number state author {{ login }} mergeCommit {{ oid }} }} pageInfo {{ hasNextPage }} }} }} }}' for i, sha in enumerate(batch))
        return github(f'query {{ repository(owner:{json.dumps(owner)}, name:{json.dumps(name)}) {{ {fields} }} }}')

    batches = [commits[offset:offset + 50] for offset in range(0, len(commits), 50)]
    with ThreadPoolExecutor(max_workers=4) as executor:
        for batch, data in zip(batches, executor.map(resolve, batches)):
            for i, sha in enumerate(batch):
                info = data[f'c{i}']
                if info is None:
                    raise RuntimeError(f'GitHub cannot resolve {sha}; check --repo.')
                identities[sha] = info
                associated = info['associatedPullRequests']
                if associated['pageInfo']['hasNextPage'] or info.get('authors', {}).get('pageInfo', {}).get('hasNextPage'):
                    raise RuntimeError(f'Too many authors or associated PRs for {sha}; refusing incomplete stats.')
                for pr in associated['nodes']:
                    if pr['state'] == 'MERGED' and (pr['mergeCommit'] or {}).get('oid') in reachable:
                        prs[pr['number']] = pr
            print(f'Resolved {len(identities)}/{len(commits)} commits', flush=True)
    people = tomllib.loads((ROOT / 'contributors/people.toml').read_text()).get('people', {})
    verified = verified_entries(people, identities)
    contributors = {}
    # Merge-only contributors still belong in the credits, even though merges
    # contribute no non-merge commit or line totals.
    for info in identities.values():
        if info.get('authors'):
            for login in human_logins(info):
                contributors.setdefault(login, blank(login))
    models = {}
    unattributed = 0
    nonmerge = git('rev-list', '--no-merges', head).splitlines()
    for sha in nonmerge:
        info = identities[sha]
        logins = human_logins(info)
        date = utc(git('show', '-s', '--format=%aI', sha))
        message = git('show', '-s', '--format=%B', sha)
        author = git('show', '-s', '--format=%an <%ae>', sha)
        author_models = model_credits('Co-Authored-By: ' + author)
        commit_models = model_credits(message)
        # A specific trailer identifies the model more precisely than a generic
        # model author. Model authors belong on Models, never the human list.
        for model in author_models:
            if not any(m[0] == model[0] for m in commit_models):
                commit_models.add(model)
        stats = collections.Counter()
        for line in git('show', '--format=', '--numstat', '-z', '--no-renames', sha).split('\0'):
            if not line:
                continue
            added, deleted, path = line.split('\t', 2)
            if added == '-':
                # Count binary files present before/after this change.
                for revision, key in [(sha, 'binary_added'), (sha + '^', 'binary_deleted')]:
                    exists = subprocess.run(['git', 'cat-file', '-e', f'{revision}:{path}'], cwd=ROOT, capture_output=True).returncode == 0
                    stats[key] += int(exists)
            else:
                stats['lines_added'] += int(added)
                stats['lines_deleted'] += int(deleted)
        if not info.get('authors') and author_models:
            logins.clear()
        for login in logins:
            row = contributors.setdefault(login, blank(login))
            row['commits'] += 1
            for key, value in stats.items():
                row[key] += value
            row['first_commit'] = min(row['first_commit'] or date, date)
            row['last_commit'] = max(row['last_commit'], date)
        if not logins and not author_models:
            unattributed += 1
        for company, model, version in commit_models:
            key = (company, model, version)
            row = models.setdefault(key, dict(id='/'.join(filter(None, [company.lower(), model.lower().replace(' ', '-'), version])), company=company, model=model, version=version, commits=0, lines_added=0, lines_deleted=0))
            for field in ('lines_added', 'lines_deleted'):
                row[field] += stats[field]
            row['commits'] += 1
    for pr in prs.values():
        if pr['author'] and pr['author']['login']:
            login = pr['author']['login']
            contributors.setdefault(login, blank(login))['prs'] += 1
    for login, row in contributors.items():
        row['people_entry'] = login in verified
        if login in verified and people[login].get('sync_github_name'):
            user = json.loads(subprocess.check_output(['gh', 'api', f'users/{login}'], text=True))
            row['github_name'] = user.get('name')
    result = dict(schema=1, repo=repo, branch=ref if ref != 'HEAD' else (git('branch', '--show-current') or 'HEAD'),
                  head=head, head_date=utc(git('show', '-s', '--format=%cI', head)),
                  commits=len(nonmerge), unattributed_commits=unattributed,
                  contributors=sorted(contributors.values(), key=lambda c: (not bool(c['first_commit']), c['first_commit'], c['login'].lower())),
                  models=sorted(models.values(), key=lambda m: (-m['commits'], m['id'])))
    target = ROOT / 'contributors/contributors.json'
    temporary = target.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(result, indent=2) + '\n')
    temporary.replace(target)
    print(f'Wrote {len(contributors)} contributors, {len(nonmerge)} commits, {len(models)} models; {unattributed} unattributed commits.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', default='storytold/effectcraft', help='GitHub repository containing the local history')
    parser.add_argument('--ref', default='HEAD', help='Fetched git revision to include (default: HEAD)')
    args = parser.parse_args()
    refresh(args.repo, args.ref)
