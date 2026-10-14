# Releasing to PyPI

Releases publish automatically when a version tag is pushed. The workflow is `.github/workflows/release.yml`. It uses PyPI trusted publishing, so no password or API token is stored in GitHub.

## One-time setup (about 5 minutes)

1. Create a free account at [pypi.org](https://pypi.org/account/register/) and turn on two-factor authentication.
2. Go to **Your account › Publishing › Add a new pending publisher**, choose **GitHub**, and enter:
   - PyPI project name: `probative-labs`
   - Owner: `VivekReddyGaddam`
   - Repository name: `probative-labs`
   - Workflow name: `release.yml`
   - Environment name: `pypi`
3. Save. The project name is reserved for this repository until the first release creates it.

## Each release

1. Update `version` in `pyproject.toml` and `__version__` in `src/probative/__init__.py`. They must match.
2. Commit, then tag and push:

   ```bash
   git tag v0.1.0
   git push origin v0.1.0
   ```

3. Watch **Actions › Release to PyPI**. The workflow runs the tests, builds, checks that the tag matches the version, and publishes.
4. Check that it worked: `pip install probative-labs==0.1.0`.

A version can only be published once. If something goes wrong after publishing, release a new patch version (for example 0.1.1).
