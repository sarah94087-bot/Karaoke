"""T-3.13: the smoke test's requests match the API it is checking.

This exists because of a specific embarrassment. The first credentialed run of
`scripts/smoke.py` against the deployment reported **two failures** - an
over-quota upload that was not refused and a ticket that could not be got - and
both were the script's own field names: `size_bytes` where the API takes
`bytes`, and `key` where it takes `upload_key`. The API answered `422` and the
script called it a broken deployment.

Chapter 14 says a failed smoke check means roll back. A smoke test that can
fail for its own reasons is therefore worse than no smoke test, because the
rollback it triggers is real. These four assertions cost nothing and make that
particular lie impossible.
"""

from apps.api.routers.songs import SongFromUpload, UploadTicket, UploadUrlRequest
from scripts.smoke import song_from_upload_payload, upload_ticket_payload


def test_the_upload_ticket_request_matches_the_model():
    payload = upload_ticket_payload("song.mp3", 1024)

    assert set(payload) == set(UploadUrlRequest.model_fields)
    # And it must actually validate, not merely have the right key names.
    assert UploadUrlRequest(**payload).bytes == 1024


def test_the_song_request_matches_the_model():
    payload = song_from_upload_payload("uploads/abc/original.mp3", "song.mp3")

    assert set(payload) == set(SongFromUpload.model_fields)
    assert SongFromUpload(**payload).upload_key == "uploads/abc/original.mp3"


def test_the_script_reads_the_ticket_fields_the_api_sends():
    """The other direction: the script PUTs to `url` with `method` and hands
    `key` back. A rename on the API side would be a live failure otherwise."""
    for field in ("key", "url", "method"):
        assert field in UploadTicket.model_fields


def test_header_names_are_matched_regardless_of_case():
    """The second thing the first live run got wrong, and the same shape as the
    first: B2 answers a preflight with `access-control-allow-origin` in lower
    case, the script looked it up by the specification's spelling, and a bucket
    that was configured correctly - 200, the rule in place, the browser
    uploading through it all day - was reported as refusing the request, with
    advice to re-run `bucket_cors.py` on it."""
    from email.message import Message

    from scripts.smoke import lowercase

    headers = Message()
    headers["access-control-allow-origin"] = "https://example.test"
    headers["Content-Type"] = "audio/mpeg"

    normalised = lowercase(headers)

    assert normalised["access-control-allow-origin"] == "https://example.test"
    assert normalised["content-type"] == "audio/mpeg"
