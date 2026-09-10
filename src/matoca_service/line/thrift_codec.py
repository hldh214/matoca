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


@dataclass(frozen=True, slots=True)
class LiffViewRequest:
    liff_id: str
    line_user_id: str
    adid: str
    line_entry_url: str


@dataclass(frozen=True, slots=True)
class LiffViewReply:
    access_token: str = field(repr=False)
    id_token: str = field(repr=False)
    context_token: str = field(repr=False)
    expires_in: int


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


def encode_issue_liff_view_call(request: LiffViewRequest) -> bytes:
    transport = TMemoryBuffer()
    protocol = TCompactProtocol(transport)
    protocol.writeMessageBegin("issueLiffView", TMessageType.CALL, 1)
    protocol.writeStructBegin("issueLiffView_args")
    protocol.writeFieldBegin("request", TType.STRUCT, 1)
    protocol.writeStructBegin("request")

    protocol.writeFieldBegin("liffId", TType.STRING, 1)
    protocol.writeString(request.liff_id)
    protocol.writeFieldEnd()

    protocol.writeFieldBegin("account", TType.STRUCT, 2)
    protocol.writeStructBegin("account")
    protocol.writeFieldBegin("user", TType.STRUCT, 2)
    protocol.writeStructBegin("user")
    protocol.writeFieldBegin("userId", TType.STRING, 1)
    protocol.writeString(request.line_user_id)
    protocol.writeFieldEnd()
    protocol.writeFieldStop()
    protocol.writeStructEnd()
    protocol.writeFieldEnd()
    protocol.writeFieldStop()
    protocol.writeStructEnd()
    protocol.writeFieldEnd()

    protocol.writeFieldBegin("device", TType.STRUCT, 4)
    protocol.writeStructBegin("device")
    protocol.writeFieldBegin("enabled", TType.BOOL, 1)
    protocol.writeBool(True)
    protocol.writeFieldEnd()
    protocol.writeFieldBegin("advertising", TType.STRUCT, 2)
    protocol.writeStructBegin("advertising")
    protocol.writeFieldBegin("adid", TType.STRING, 1)
    protocol.writeString(request.adid)
    protocol.writeFieldEnd()
    protocol.writeFieldBegin("trackingEnabled", TType.BOOL, 2)
    protocol.writeBool(True)
    protocol.writeFieldEnd()
    protocol.writeFieldStop()
    protocol.writeStructEnd()
    protocol.writeFieldEnd()
    protocol.writeFieldBegin("miniApp", TType.BOOL, 3)
    protocol.writeBool(True)
    protocol.writeFieldEnd()
    protocol.writeFieldStop()
    protocol.writeStructEnd()
    protocol.writeFieldEnd()

    protocol.writeFieldBegin("externalBrowser", TType.BOOL, 6)
    protocol.writeBool(False)
    protocol.writeFieldEnd()
    protocol.writeFieldBegin("miniDomain", TType.STRING, 7)
    protocol.writeString("miniapp.line.me")
    protocol.writeFieldEnd()
    protocol.writeFieldBegin("entryUrl", TType.STRING, 9)
    protocol.writeString(request.line_entry_url)
    protocol.writeFieldEnd()

    protocol.writeFieldStop()
    protocol.writeStructEnd()
    protocol.writeFieldEnd()
    protocol.writeFieldStop()
    protocol.writeStructEnd()
    protocol.writeMessageEnd()
    return cast(bytes, transport.getvalue())


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


def _read_liff_success(protocol: TCompactProtocol) -> LiffViewReply:
    context_token: str | None = None
    access_token: str | None = None
    id_token: str | None = None
    expires_in: int | None = None
    protocol.readStructBegin()
    while True:
        _, field_type, field_id = protocol.readFieldBegin()
        if field_type == TType.STOP:
            break
        if field_id == 2 and field_type == TType.STRING:
            context_token = protocol.readString()
        elif field_id == 3 and field_type == TType.STRING:
            access_token = protocol.readString()
        elif field_id == 7 and field_type == TType.STRING:
            id_token = protocol.readString()
        elif field_id == 13 and field_type == TType.I64:
            expires_in = protocol.readI64()
        else:
            protocol.skip(field_type)
        protocol.readFieldEnd()
    protocol.readStructEnd()

    if None in (context_token, access_token, id_token, expires_in):
        raise LineThriftError("LIFF reply is missing a required field")
    return LiffViewReply(
        context_token=cast(str, context_token),
        access_token=cast(str, access_token),
        id_token=cast(str, id_token),
        expires_in=cast(int, expires_in),
    )


def decode_liff_view_reply(data: bytes) -> LiffViewReply:
    protocol = TCompactProtocol(TMemoryBuffer(data))
    try:
        method, message_type, _ = protocol.readMessageBegin()
        if method != "issueLiffView":
            raise LineThriftError("unexpected Thrift method in LIFF reply")
        if message_type == TMessageType.EXCEPTION:
            exception = TApplicationException()
            exception.read(protocol)
            raise LineThriftError("LINE returned a Thrift application exception")
        if message_type != TMessageType.REPLY:
            raise LineThriftError("unexpected Thrift message type in LIFF reply")

        result: LiffViewReply | None = None
        protocol.readStructBegin()
        while True:
            _, field_type, field_id = protocol.readFieldBegin()
            if field_type == TType.STOP:
                break
            if field_id == 0 and field_type == TType.STRUCT:
                result = _read_liff_success(protocol)
            else:
                protocol.skip(field_type)
            protocol.readFieldEnd()
        protocol.readStructEnd()
        protocol.readMessageEnd()
    except LineThriftError:
        raise
    except Exception as error:
        raise LineThriftError("invalid LIFF Thrift reply") from error

    if result is None:
        raise LineThriftError("LIFF reply does not contain a success result")
    return result
