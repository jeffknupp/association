Releasing
=========

Distribution is automated by ``.github/workflows/publish.yml``. Publishing uses
PyPI's `Trusted Publishing
<https://docs.pypi.org/trusted-publishers/>`_, so there is no API token stored
in the repository: PyPI verifies the GitHub Actions workflow identity over OIDC
at upload time.

One-time setup
--------------

This part cannot be automated, and must be done before the first release.

#. On PyPI, go to *Your projects* → *Publishing* (or, for a project that does
   not exist yet, *Add a pending publisher*) and register a publisher with:

   :Owner: ``jeffknupp``
   :Repository: ``association``
   :Workflow: ``publish.yml``
   :Environment: ``pypi``

#. Repeat on `TestPyPI <https://test.pypi.org>`_ with the environment
   ``testpypi``, so a release can be rehearsed against a throwaway index.

#. In the GitHub repository settings, create the ``pypi`` and ``testpypi``
   environments. Adding a required reviewer to ``pypi`` is worthwhile: it makes
   the actual upload a deliberate, approved step rather than a side effect of
   publishing a release.

Cutting a release
-----------------

#. Bump ``version`` in ``pyproject.toml``.
#. Add a ``CHANGES.md`` entry describing the change (the ``changes-md``
   pre-commit hook enforces that one exists for any commit touching ``src/``).
#. Commit and push.
#. Rehearse if the packaging itself changed: run the *Publish* workflow
   manually with the target ``testpypi``, then check the result installs::

      $ uv run --with association --index-url https://test.pypi.org/simple/ \
            --extra-index-url https://pypi.org/simple/ association --help

#. Create a GitHub release tagged ``vX.Y.Z``. Publishing the release triggers
   the workflow, which runs the full gate suite, builds, and uploads.

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
