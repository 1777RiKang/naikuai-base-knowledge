"""Download a pinned GitHub Release into a separate public cache, without deployment."""
import argparse
from pathlib import Path
import sys
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'public/scripts'))
from release_cache import REPOSITORY, parse_manifest, stage_release


def manifest_base(url):
    parsed = urllib.parse.urlsplit(url)
    prefix = '/' + REPOSITORY + '/releases/download/'
    if (parsed.scheme != 'https' or parsed.netloc != 'github.com' or parsed.query or parsed.fragment
            or not parsed.path.startswith(prefix) or not parsed.path.endswith('/manifest.json')):
        raise ValueError('expected a manifest from the configured GitHub repository release')
    tag = parsed.path[len(prefix):].split('/')
    if len(tag) != 2 or not tag[0].startswith('v'):
        raise ValueError('invalid release tag path')
    return url.rsplit('/', 1)[0]


def download(url, maximum):
    # GitHub serves release assets through its HTTPS asset hosts.
    with urllib.request.urlopen(url, timeout=30) as response:
        final = urllib.parse.urlsplit(response.url)
        if final.scheme != 'https' or not (final.hostname == 'github.com' or
                (final.hostname or '').endswith('.githubusercontent.com')):
            raise ValueError('unexpected release redirect')
        data = response.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError('release asset exceeds size limit')
    return data


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest_url')
    parser.add_argument('--manifest-sha256', required=True, help='Digest from the reviewed release notes')
    parser.add_argument('--cache-dir', type=Path, default=ROOT / '.local/public-r3-releases')
    args = parser.parse_args()
    base = manifest_base(args.manifest_url)
    data = download(args.manifest_url, 128 * 1024)
    manifest = parse_manifest(data, args.manifest_sha256)
    if base.rsplit('/', 1)[-1] != 'v' + manifest['release_version']:
        raise ValueError('release tag and manifest version differ')
    target = stage_release(data, args.manifest_sha256, lambda name, size: download(base+'/'+name, size), args.cache_dir)
    print('Verified public release cached at ' + str(target))
    print('No bot configuration or private database was modified; runtime activation is a separate operation.')
