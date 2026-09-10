from thrift.protocol.TCompactProtocol import TCompactProtocol
from thrift.Thrift import TMessageType, TType
from thrift.transport.TTransport import TMemoryBuffer

from matoca_service.line.thrift_codec import (
    LiffViewRequest,
    decode_liff_view_reply,
    decode_refresh_reply,
    encode_issue_liff_view_call,
    encode_refresh_call,
    encode_report_refreshed_access_token_call,
)

REFRESH_REQUEST_GOLDEN = bytes.fromhex(
    "82210107726566726573681c181773796e7468657469632d726566726573682d746f6b656e0000"
)
REPORT_REQUEST_GOLDEN = bytes.fromhex(
    "8221011a7265706f7274526566726573686564416363657373546f6b656e"
    "1c181673796e7468657469632d6163636573732d746f6b656e0000"
)


def build_refresh_reply(*, include_refresh_token: bool = True) -> bytes:
    transport = TMemoryBuffer()
    protocol = TCompactProtocol(transport)
    protocol.writeMessageBegin("refresh", TMessageType.REPLY, 1)
    protocol.writeStructBegin("refresh_result")
    protocol.writeFieldBegin("success", TType.STRUCT, 0)
    protocol.writeStructBegin("result")
    protocol.writeFieldBegin("accessToken", TType.STRING, 1)
    protocol.writeString("synthetic-access-token")
    protocol.writeFieldEnd()
    protocol.writeFieldBegin("unknownNested", TType.STRUCT, 3)
    protocol.writeStructBegin("unknown")
    protocol.writeFieldBegin("value", TType.I64, 1)
    protocol.writeI64(200)
    protocol.writeFieldEnd()
    protocol.writeFieldStop()
    protocol.writeStructEnd()
    protocol.writeFieldEnd()
    if include_refresh_token:
        protocol.writeFieldBegin("refreshToken", TType.STRING, 5)
        protocol.writeString("synthetic-refresh-token")
        protocol.writeFieldEnd()
    protocol.writeFieldStop()
    protocol.writeStructEnd()
    protocol.writeFieldEnd()
    protocol.writeFieldStop()
    protocol.writeStructEnd()
    protocol.writeMessageEnd()
    return transport.getvalue()


def build_liff_reply() -> bytes:
    transport = TMemoryBuffer()
    protocol = TCompactProtocol(transport)
    protocol.writeMessageBegin("issueLiffView", TMessageType.REPLY, 1)
    protocol.writeStructBegin("issueLiffView_result")
    protocol.writeFieldBegin("success", TType.STRUCT, 0)
    protocol.writeStructBegin("result")
    protocol.writeFieldBegin("contextToken", TType.STRING, 2)
    protocol.writeString("synthetic-context-token")
    protocol.writeFieldEnd()
    protocol.writeFieldBegin("accessToken", TType.STRING, 3)
    protocol.writeString("synthetic-liff-access-token")
    protocol.writeFieldEnd()
    protocol.writeFieldBegin("idToken", TType.STRING, 7)
    protocol.writeString("synthetic-id-token")
    protocol.writeFieldEnd()
    protocol.writeFieldBegin("expiresIn", TType.I64, 13)
    protocol.writeI64(43_200)
    protocol.writeFieldEnd()
    protocol.writeFieldBegin("unknown", TType.LIST, 99)
    protocol.writeListBegin(TType.I32, 2)
    protocol.writeI32(1)
    protocol.writeI32(2)
    protocol.writeListEnd()
    protocol.writeFieldEnd()
    protocol.writeFieldStop()
    protocol.writeStructEnd()
    protocol.writeFieldEnd()
    protocol.writeFieldStop()
    protocol.writeStructEnd()
    protocol.writeMessageEnd()
    return transport.getvalue()


def test_refresh_call_matches_synthetic_golden_bytes() -> None:
    assert encode_refresh_call("synthetic-refresh-token") == REFRESH_REQUEST_GOLDEN


def test_report_call_matches_synthetic_golden_bytes() -> None:
    assert (
        encode_report_refreshed_access_token_call("synthetic-access-token") == REPORT_REQUEST_GOLDEN
    )


def test_refresh_reply_extracts_typed_tokens_and_skips_unknown_fields() -> None:
    result = decode_refresh_reply(build_refresh_reply())

    assert result.access_token == "synthetic-access-token"
    assert result.refresh_token == "synthetic-refresh-token"


def test_issue_liff_view_call_contains_captured_request_fields() -> None:
    request = LiffViewRequest(
        liff_id="2006055787-m6P6OJ38",
        line_user_id="u-synthetic",
        adid="device-id",
        line_entry_url="line://app/2006055787-m6P6OJ38?liff.state=%2Fwaiting%2F",
    )

    encoded = encode_issue_liff_view_call(request)

    assert b"issueLiffView" in encoded
    assert request.liff_id.encode() in encoded
    assert request.line_user_id.encode() in encoded
    assert request.adid.encode() in encoded
    assert b"miniapp.line.me" in encoded
    assert request.line_entry_url.encode() in encoded


def test_liff_reply_extracts_access_token_and_lifetime() -> None:
    result = decode_liff_view_reply(build_liff_reply())

    assert result.access_token == "synthetic-liff-access-token"
    assert result.id_token == "synthetic-id-token"
    assert result.context_token == "synthetic-context-token"
    assert result.expires_in == 43_200
