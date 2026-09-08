Releasing
=========

Distribution is automated by ``.github/workflows/publish.yml``. Publishing uses
PyPI's `Trusted Publishing
<https://docs.pypi.org/trusted-publishers/>`_, so there is no API token stored
in the repository: PyPI verifies the GitHub Actions workflow identity over OIDC
at upload time.

Versioning
----------

Released versions follow `semantic versioning <https://semver.org>`_. The
compatibility promise covers the CLI surface — command and option names, and
output shapes a script might parse — and the documented Python API. It does not
cover the query templates or the router's intent set, which are expected to
grow continuously; a new intent is a minor bump, not a major one.

``pyproject.toml`` holds the only copy of the number. The package reads it back
through :func:`importlib.metadata.version` and exposes it as
``association.__version__``; ``docs/conf.py`` imports that; the CLI reports it
through ``--version``; and the publish workflow parses the same file to check
the tag agrees. Nothing else needs editing on a bump.

Cutting a release
-----------------

#. Describe the change under a ``## Unreleased`` heading in ``CHANGES.md``.
   This is a hard requirement, not a convention — the bump script stops if the
   section is missing, because a release with no description of what changed is
   worse than a release that failed to happen.

#. Bump, commit and tag::

      $ scripts/bump_version.py minor --tag

   Accepts ``major``, ``minor``, ``patch`` or an explicit ``X.Y.Z``. It renames
   the ``## Unreleased`` heading to the version and today's date, refuses to run
   on a dirty working tree, and refuses to reuse a tag that already exists —
   PyPI would not accept a second upload for that version either.

#. Rehearse against TestPyPI if the packaging itself changed: run the *Publish*
   workflow manually with the target ``testpypi``, then check the result
   installs::

      $ uv run --with association --index-url https://test.pypi.org/simple/ \
            --extra-index-url https://pypi.org/simple/ association --version

#. Push, then create the release::

      $ git push && git push --tags
      $ scripts/release.sh X.Y.Z

   ``release.sh`` re-checks that the packaged version, the local tag and the
   pushed tag all agree, uses the changelog section for that version as the
   release notes so the two cannot disagree, and prompts before creating
   anything. Pass ``--draft`` to stage the release without triggering the
   upload.

Nothing before the final step is irreversible. The bump script never pushes,
and a local tag can be deleted; publishing the GitHub release is the point of
no return, because it triggers the PyPI upload.

What the workflow guards
------------------------

Every check that gates a normal commit also gates a release — ruff, both mypy
passes, ``pyright --verifytypes`` at 100%, docstring coverage and the tests —
rather than trusting that CI happened to be green on the tagged commit. Two
release-specific checks run on top of those:

**The tag must match the packaged version.** Tagging ``v0.2.0`` while
``pyproject.toml`` still reads ``0.1.0`` would otherwise upload a second
``0.1.0``, which PyPI rejects as a duplicate — after the tag has been spent.

**Metadata must render.** ``twine check --strict`` runs on the built artifacts,
because PyPI rejects a README it cannot render, and does so at upload time.

Version numbers are never reused: PyPI refuses to accept a file for a version
that already exists, even after a deletion, so a broken release is fixed by
publishing the next patch version rather than by re-uploading.

Hosted documentation
--------------------

``.readthedocs.yaml`` builds this site on Read the Docs on every push, and on
pull requests as a preview. It installs from ``uv.lock`` with ``--frozen``
rather than letting a resolver pick fresh versions, because the toolchain has
already been broken once by an unpinned upgrade — Sphinx 9 is incompatible
with ``sphinx-click``, hence the ``sphinx>=8.1,<9`` pin — and a published build
that quietly drifts from what CI verified is worse than one that fails loudly.

The build runs ``scripts/build_docs.sh``, the same script pre-commit and CI
run, so the ``-W --keep-going`` that gates a commit also gates the published
build.
