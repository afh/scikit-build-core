from __future__ import annotations

import contextlib
import dataclasses
import importlib.util
import os
import shutil
import subprocess
import sys
import types
import warnings
from collections.abc import Generator
from pathlib import Path
from venv import EnvBuilder

if sys.version_info < (3, 8):
    import importlib_metadata as metadata
    from typing_extensions import Literal, overload
else:
    from importlib import metadata
    from typing import Literal, overload

import pytest

DIR = Path(__file__).parent.resolve()
BASE = DIR.parent


@pytest.fixture(scope="session")
def pep518_wheelhouse(tmp_path_factory: pytest.TempPathFactory) -> Path:
    wheelhouse = tmp_path_factory.mktemp("wheelhouse")

    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--wheel-dir",
            str(wheelhouse),
            f"{BASE}[pyproject]",
        ],
        check=True,
    )
    packages = [
        "build",
        "hatchling",
        "pip>=23",
        "pybind11",
        "rich",
        "setuptools",
        "virtualenv",
        "wheel",
    ]

    if importlib.util.find_spec("cmake") is not None:
        packages.append("cmake")

    if importlib.util.find_spec("ninja") is not None:
        packages.append("ninja")

    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "download",
            "-q",
            "-d",
            str(wheelhouse),
            *packages,
        ],
        check=True,
    )
    return wheelhouse


class VEnv(EnvBuilder):
    executable: Path
    env_dir: Path
    wheelhouse: Path | None

    def __init__(self, env_dir: Path, *, wheelhouse: Path | None = None) -> None:
        super().__init__(with_pip=True)
        # This warning is mistakenly generated by CPython 3.11.0
        # https://github.com/python/cpython/pull/98743
        with warnings.catch_warnings():
            if sys.version_info[:3] == (3, 11, 0):
                warnings.filterwarnings(
                    "ignore",
                    "check_home argument is deprecated and ignored.",
                    DeprecationWarning,
                )
            self.create(env_dir)
        self.wheelhouse = None
        self.install("pip>=21.3.1")
        self.wheelhouse = wheelhouse

    def ensure_directories(
        self, env_dir: str | bytes | os.PathLike[str] | os.PathLike[bytes]
    ) -> types.SimpleNamespace:
        context = super().ensure_directories(env_dir)
        # Store the path to the venv Python interpreter.
        # See https://github.com/mesonbuild/meson-python/blob/8a180be7b4abd7e1939a63d5d59f63197ee27cc7/tests/conftest.py#LL79
        self.executable = Path(context.env_exe)
        self.env_dir = Path(context.env_dir)
        return context

    @overload
    def run(self, *args: str, capture: Literal[True]) -> str:
        ...

    @overload
    def run(self, *args: str, capture: Literal[False] = ...) -> None:
        ...

    def run(self, *args: str, capture: bool = False) -> str | None:
        __tracebackhide__ = True
        env = os.environ.copy()
        env["PATH"] = f"{self.executable.parent}{os.pathsep}{env['PATH']}"
        env["VIRTUAL_ENV"] = str(self.env_dir)
        env["PIP_DISABLE_PIP_VERSION_CHECK"] = "ON"
        if self.wheelhouse is not None:
            env["PIP_NO_INDEX"] = "ON"
            env["PIP_FIND_LINKS"] = str(self.wheelhouse)

        str_args = [os.fspath(a) for a in args]

        if capture:
            result = subprocess.run(
                str_args,
                check=False,
                capture_output=True,
                text=True,
                env=env,
            )
            if result.returncode != 0:
                print(result.stdout, file=sys.stdout)
                print(result.stderr, file=sys.stderr)
                print("FAILED RUN:", *str_args, file=sys.stderr)
                raise SystemExit(result.returncode)
            return result.stdout.strip()

        result_bytes = subprocess.run(
            str_args,
            check=False,
            env=env,
        )
        if result_bytes.returncode != 0:
            print("FAILED RUN:", *str_args, file=sys.stderr)
            raise SystemExit(result_bytes.returncode)
        return None

    def execute(self, command: str) -> str:
        return self.run(str(self.executable), "-c", command, capture=True)

    def module(self, *args: str) -> None:
        return self.run(str(self.executable), "-m", *args)

    def install(self, *args: str, isolated: bool = True) -> None:
        isolated_flags = "" if isolated else ["--no-build-isolation"]
        self.module("pip", "install", *isolated_flags, *args)


@pytest.fixture()
def isolated(tmp_path: Path, pep518_wheelhouse: Path) -> Generator[VEnv, None, None]:
    path = tmp_path / "venv"
    try:
        yield VEnv(path, wheelhouse=pep518_wheelhouse)
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture()
def virtualenv(tmp_path: Path) -> Generator[VEnv, None, None]:
    path = tmp_path / "venv"
    try:
        yield VEnv(path)
    finally:
        shutil.rmtree(path, ignore_errors=True)


@dataclasses.dataclass(frozen=True)
class PackageInfo:
    name: str
    sdist_hash38: str | None = None
    sdist_hash39: str | None = None
    sdist_dated_hash39: str | None = None
    sdist_dated_hash38: str | None = None

    @property
    def sdist_hash(self) -> str | None:
        return self.sdist_hash38 if sys.version_info < (3, 9) else self.sdist_hash39

    @property
    def sdist_dated_hash(self) -> str | None:
        return (
            self.sdist_dated_hash38
            if sys.version_info < (3, 9)
            else self.sdist_dated_hash39
        )

    @property
    def source_date_epoch(self) -> str:
        return "12345"


def process_package(
    package: PackageInfo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package_dir = tmp_path / "pkg"
    shutil.copytree(DIR / "packages" / package.name, package_dir)
    monkeypatch.chdir(package_dir)
    # Just in case this gets littered into the source tree, clear it out
    if Path("dist").is_dir():
        shutil.rmtree("dist")


@pytest.fixture()
def package_simple_pyproject_ext(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> PackageInfo:
    package = PackageInfo(
        "simple_pyproject_ext",
        "f1b86c7fbcc70ed82786fd3446caf880091096b7d6c0085eab8fe64466b95c4f",
        "463bdfcfad8b71a0f2b48b7b5abea191c9073170326183c04b7f23da19d6b61b",
        "aa1f2cd959998cb58316f72526ad7b2d3078bf47d00c5c9f8903d9b5980c0e35",
        "9e4713843829659b4862e73c8a9ae783178d620a78fed1f757efb82ea77ff82f",
    )
    process_package(package, tmp_path, monkeypatch)
    return package


@pytest.fixture()
def package_simple_setuptools_ext(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> PackageInfo:
    package = PackageInfo("simple_setuptools_ext")
    process_package(package, tmp_path, monkeypatch)
    return package


@pytest.fixture()
def package_filepath_pure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> PackageInfo:
    package = PackageInfo("filepath_pure")
    process_package(package, tmp_path, monkeypatch)
    return package


@pytest.fixture()
def package_dynamic_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> PackageInfo:
    package = PackageInfo("dynamic_metadata")
    process_package(package, tmp_path, monkeypatch)
    return package


@pytest.fixture()
def package_simplest_c(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PackageInfo:
    package = PackageInfo(
        "simplest_c",
    )
    process_package(package, tmp_path, monkeypatch)
    return package


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        # Ensure all tests using virtualenv are marked as such
        if "virtualenv" in getattr(item, "fixturenames", ()):
            item.add_marker(pytest.mark.virtualenv)
        if "isolated" in getattr(item, "fixturenames", ()):
            item.add_marker(pytest.mark.virtualenv)
            item.add_marker(pytest.mark.isolated)


def pytest_report_header() -> str:
    interesting_packages = [
        "build",
        "packaging",
        "pathspec",
        "pip",
        "pybind11",
        "pyproject_metadata",
        "rich",
        "scikit_build_core",
        "setuptools",
        "virtualenv",
        "wheel",
    ]
    valid = []
    for package in interesting_packages:
        with contextlib.suppress(ModuleNotFoundError):
            valid.append(f"{package}=={metadata.version(package)}")  # type: ignore[no-untyped-call]
    reqs = " ".join(valid)
    return f"installed packages of interest: {reqs}"
