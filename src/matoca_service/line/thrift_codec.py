from dataclasses import dataclass, field
from typing import cast

from thrift.protocol.TCompactProtocol import TCompactProtocol  # type: ignore[import-untyped]
from thrift.Thrift import TApplicationException, TMessageType, TType  # type: ignore[import-untyped]
from thrift.transport.TTransport import TMemoryBuffer  # type: ignore[import-untyped]


class LineThriftError(ValueError):
    """Raised when a LINE Thrift message does not match the captured contract."""


@dataclass(frozen=True, slots=True)
class RefreshReply:
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)


def _encode_single_string_call(method: str, value: str) -> bytes:
    transport = TMemoryBuffer()
    protocol = TCompactProtocol(transport)
    protocol.writeMessageBegin(method, TMessageType.CALL, 1)
    protocol.writeStructBegin(f"{method}_args")
    protocol.writeFieldBegin("request", TType.STRUCT, 1)
    protocol.writeStructBegin("request")
    protocol.writeFieldBegin("token", TType.STRING, 1)
    protocol.writeString(value)
    protocol.writeFieldEnd()
    protocol.writeFieldStop()
    protocol.writeStructEnd()
    protocol.writeFieldEnd()
    protocol.writeFieldStop()
    protocol.writeStructEnd()
    protocol.writeMessageEnd()
    return cast(bytes, transport.getvalue())


def encode_refresh_call(refresh_token: str) -> bytes:
    return _encode_single_string_call("refresh", refresh_token)


def encode_report_refreshed_access_token_call(access_token: str) -> bytes:
    return _encode_single_string_call("reportRefreshedAccessToken", access_token)


def _read_refresh_success(protocol: TCompactProtocol) -> RefreshReply:
    access_token: str | None = None
    refresh_token: str | None = None
    protocol.readStructBegin()
    while True:
        _, field_type, field_id = protocol.readFieldBegin()
        if field_type == TType.STOP:
            break
        if field_id == 1 and field_type == TType.STRING:
            access_token = protocol.readString()
        elif field_id == 5 and field_type == TType.STRING:
            refresh_token = protocol.readString()
        else:
            protocol.skip(field_type)
        protocol.readFieldEnd()
    protocol.readStructEnd()

    if access_token is None or refresh_token is None:
        raise LineThriftError("refresh reply is missing a required credential field")
    return RefreshReply(access_token=access_token, refresh_token=refresh_token)


def decode_refresh_reply(data: bytes) -> RefreshReply:
    protocol = TCompactProtocol(TMemoryBuffer(data))
    try:
        method, message_type, _ = protocol.readMessageBegin()
        if method != "refresh":
            raise LineThriftError("unexpected Thrift method in refresh reply")
        if message_type == TMessageType.EXCEPTION:
            exception = TApplicationException()
            exception.read(protocol)
            raise LineThriftError("LINE returned a Thrift application exception")
        if message_type != TMessageType.REPLY:
            raise LineThriftError("unexpected Thrift message type in refresh reply")

        result: RefreshReply | None = None
        protocol.readStructBegin()
        while True:
            _, field_type, field_id = protocol.readFieldBegin()
            if field_type == TType.STOP:
                break
            if field_id == 0 and field_type == TType.STRUCT:
                result = _read_refresh_success(protocol)
            else:
                protocol.skip(field_type)
            protocol.readFieldEnd()
        protocol.readStructEnd()
        protocol.readMessageEnd()
    except LineThriftError:
        raise
    except Exception as error:
        raise LineThriftError("invalid refresh Thrift reply") from error

    if result is None:
        raise LineThriftError("refresh reply does not contain a success result")
    return result
