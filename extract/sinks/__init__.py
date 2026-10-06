from extract.sinks.base import Sink, to_record
from extract.sinks.local import LocalJsonlSink
from extract.sinks.postgres import PostgresSink

__all__ = ["Sink", "to_record", "LocalJsonlSink", "PostgresSink"]
