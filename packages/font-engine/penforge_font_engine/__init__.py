"""Independent font engine. Heavy parsers load only inside explicit compiler calls."""


def compile_font(*args, **kwargs):
    from .compiler import compile_font as run

    return run(*args, **kwargs)


def coverage(*args, **kwargs):
    from .compiler import coverage as run

    return run(*args, **kwargs)


def installed_versions():
    from .compiler import installed_versions as run

    return run()
