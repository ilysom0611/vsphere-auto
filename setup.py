"""Fallback for ancient pip (<19) on CentOS 7 that cannot read pyproject.toml.

Modern pip (>=19) ignores this file and builds via pyproject.toml + hatchling.
Keep install_requires/python_requires in sync with pyproject.toml.
"""
from setuptools import find_packages, setup

setup(
    name="vsphere-auto",
    version="0.1.0",
    description="vSphere automated batch VM deployment with auto resource selection, idempotency and robustness",
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    python_requires=">=3.9",
    install_requires=[
        "flask>=3.0",
        "pyvmomi>=7.0",
        "pyyaml>=6.0",
        "pydantic>=2.0",
        "typer>=0.12",
        "rich>=13.0",
        "cryptography>=42.0",
        "tenacity>=8.0",
        "requests>=2.31",
        "jinja2>=3.1",
    ],
    entry_points={
        "console_scripts": [
            "vsphere-auto=vsphere_auto.cli:app",
        ],
    },
    include_package_data=True,
)
