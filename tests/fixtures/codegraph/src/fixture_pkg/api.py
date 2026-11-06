from fixture_pkg.helpers import target as aliased
from .helpers import target
from . import helpers
from fixture_pkg.helpers import duplicate as chosen


def entry():
    target()
    aliased()
    helpers.target()
    return target()


class Service:
    def run(self):
        return self.finish()

    def finish(self):
        return target()


def shadowed(target):
    return target()


def callback(fn):
    return fn()


def wire():
    return callback(target)


def choose():
    return chosen()
