import labpilot


def test_package_exposes_the_release_version() -> None:
    assert labpilot.__version__ == "0.1.0"
