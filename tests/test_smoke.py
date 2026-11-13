# tests/test_smoke.py

def test_package_is_importable():
    import quantforge
    assert quantforge is not None