<kbd height=36><img src=https://raw.githubusercontent.com/irgolic/orange3/master/distribute/icon-48.png alt=img height=36/></kbd> Orange
======

[![Discord Chat](https://img.shields.io/discord/633376992607076354?style=for-the-badge&logo=discord&color=orange&labelColor=black)](https://discord.gg/FWrfeXV)
[![build: passing](https://img.shields.io/travis/biolab/orange3?style=for-the-badge&labelColor=black)](https://travis-ci.org/biolab/orange3)
[![codecov](https://img.shields.io/codecov/c/github/biolab/orange3?style=for-the-badge&labelColor=black)](https://codecov.io/gh/biolab/orange3)

[Orange] is a component-based data mining software. It includes a range of data
visualization, exploration, preprocessing and modeling techniques. It can be
used through a nice and intuitive user interface or, for more advanced users,
as a module for the Python programming language.

This is the latest version of Orange (for Python 3). The deprecated version of Orange 2.7 (for Python 2.7) is still available ([binaries] and [sources]).

[Orange]: https://orange.biolab.si/
[binaries]: https://orange.biolab.si/orange2/
[sources]: https://github.com/biolab/orange2


Installing with Miniconda / Anaconda
------------------------------------

Orange requires Python 3.6 or newer.

First, install [Miniconda] for your OS. Create virtual environment for Orange:

```Shell
conda create python=3 --name orange3
```
In your Anaconda Prompt add conda-forge to your channels:

```Shell
conda config --add channels conda-forge
```

This will enable access to the latest Orange release. Then install Orange3:

```Shell
conda install orange3
```

[Miniconda]: https://docs.conda.io/en/latest/miniconda.html

To install the add-ons, follow a similar recipe:

```Shell
conda install orange3-<addon name>
```

See specific add-on repositories for details.

Installing with pip
-------------------

To install Orange with pip, run the following.

```Shell
# Install some build requirements via your system's package manager
sudo apt install virtualenv build-essential python3-dev

# Create a separate Python environment for Orange and its dependencies ...
virtualenv --python=python3 --system-site-packages orange3venv
# ... and make it the active one
source orange3venv/bin/activate

# Install Orange
pip install orange3
```

Installing with winget (Windows only)
-------------------------------------

To install Orange with [winget](https://docs.microsoft.com/en-us/windows/package-manager/winget/), run:

```Shell
winget install --id  UniversityofLjubljana.Orange 
```

Starting Orange GUI
-------------------

To start Orange GUI from the command line, run:

```Shell
orange-canvas
# or
python3 -m Orange.canvas
```

Append `--help` for a list of program options.
