# Ijai fixtures

Responses as reported in upstream issues and pull requests, scrubbed: every serial number,
user id, device id and URL is made up. `wifi_sn` values keep the reported length and
characters (an 18-, 19- or 20-character serial, some with a slash). The reports elide the
other entries of property 7/45 as `...`; they are placeholders here.

- `cloud_responses.json`: answers of the Xiaomi cloud API.
  - `interim_file_url_pro_no_storage`: `/v2/home/get_interim_file_url_pro` for ijai.vacuum.v3
    and v1 (upstream PR #787 comment, issue #742).
  - `interim_file_url`: `/v2/home/get_interim_file_url` with a map URL (shape of the
    `result.url` the integration reads).
  - `get_properties_*`: `/v2/home/rpc/<did>` method `get_properties` for the WiFi serial
    number properties (upstream PR #782).
- `local_properties.json`: `MiotDevice.get_property_by(siid, piid)` answers.
  - `healthy`: a vacuum that reports its serial in 1/3, as the integration expects.
  - `v1`: ijai.vacuum.v1 (issue #742): 1/3 has no value, 1/5 answers -4003, the serial is a
    19-character entry of 7/45.
  - `v3_null`: ijai.vacuum.v3 (upstream PR #787): 1/3 answers `value: null`.
  - `s20`: xiaomi.vacuum.d106gl and ijai.vacuum.v17 (issues #709 and #682): the serial in 7/45
    is `<5 digits>/<14 characters>;<owner uid>`.
  - `unreachable`: no answer on the local network (issues #698 and #714).

No real map was attached to the issues. `tests/ijai_map.py` builds maps with the parser's
own protobuf schema and encrypts them the way the vacuum does, with the key derivation of
`vacuum-map-parser-ijai`.
