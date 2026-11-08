# Redfish profile fidelity

[Documentation](readme.md) · [Architecture](architecture.md) · [Monitoring](monitoring.md)

Documentation audit dated November 8, 2026. The server now implements a
**subset of vendor-documented contracts**. This does not certify complete
emulation or an exact match with real firmware or hardware configurations.

The 267 rackhands JSON files remain unchanged and verifiable by their checksums.
The imported generator has local docstrings; its executable syntax is verified
against the original import.
Provenance, licensing and integrity checks are described in [sources.md](sources.md).
Corrections are applied in memory in `src/redfish_mock/contracts.py` and
`src/redfish_mock/inventory.py`. The `src/redfish_mock/monitoring/` package adds
monitoring data; [monitoring sources, conventions and limitations](monitoring.md)
are detailed separately. `--raw-fixtures` lets you compare responses with the
upstream JSON without these data corrections; engine rules remain the same.
`GET /_mock` exposes the validation level, including `hardware_verified: false`
and `exact_firmware_verified: false`.

## References consulted

| Identifier | Primary source | Scope and limitation |
|---|---|---|
| DELL-API | [iDRAC9 7.xx guide](https://developer.dell.com/apis/2978/versions/7.xx/docs/) | iDRAC9 family; the PDF portal could not be retrieved. The details below were verified in Dell's executable examples. |
| DELL-POWER | [GetSetPowerStateREDFISH.py](https://github.com/dell/iDRAC-Redfish-Scripting/blob/master/Redfish%20Python/GetSetPowerStateREDFISH.py) | System reset URI and success status code. |
| DELL-BOOT | [SetNextOneTimeBootDeviceREDFISH.py](https://github.com/dell/iDRAC-Redfish-Scripting/blob/master/Redfish%20Python/SetNextOneTimeBootDeviceREDFISH.py) | Boot PATCH and reading the state back. |
| DELL-MEDIA | [InsertEjectVirtualMediaREDFISH.py](https://github.com/dell/iDRAC-Redfish-Scripting/blob/master/Redfish%20Python/InsertEjectVirtualMediaREDFISH.py) | Success status codes for virtual media actions. |
| HPE-SYSTEM | [iLO 5 v3.06 — ComputerSystem](https://servermanagementportal.ext.hpe.com/docs/redfishservices/ilos/ilo5/ilo5_306/ilo5_computersystem_resourcedefns306) | Collection types, read-only properties and boot properties. This documentation version does not certify firmware 3.08 displayed by the synthetic dataset. |
| HPE-RESP | [iLO Redfish responses](https://servermanagementportal.ext.hpe.com/docs/concepts/errorresponses) | Structure of success and error messages. |
| HPE-MEDIA | [iLO 5 v3.06 — other resources, VirtualMedia](https://servermanagementportal.ext.hpe.com/docs/redfishservices/ilos/ilo5/ilo5_306/ilo5_other_resourcedefns306) | Standard and OEM actions, media fields and boolean parameters. |
| HPE-CLIENT | [HPE Python client examples](https://servermanagementportal.ext.hpe.com/docs/redfishclients/python-redfish-library/examples) | System reset response and return conventions. |
| SMC-GUIDE | [Supermicro Redfish User Guide, revision 6.1](https://www.supermicro.com/manuals/other/RedfishUserGuide.pdf) | Printed pages 177–183: logs; 192–196: sessions and reset. A document covering multiple generations, not a capture of firmware 01.73.12. |
| SMC-VM | [Supermicro FAQ 33539](https://www.supermicro.com/en/support/faqs/faq.php?faq=33539) | Distinction between virtual media path families. |
| DMTF-SYSTEM | [ComputerSystem.v1_20_0.json](https://github.com/DMTF/Redfish-Publications/blob/main/json-schema/ComputerSystem.v1_20_0.json) | Required identity and read-only PowerState. |
| DMTF-THERMAL | [Thermal.v1_7_0.json](https://github.com/DMTF/Redfish-Publications/blob/main/json-schema/Thermal.v1_7_0.json) | Fan and temperature identifiers. |
| DMTF-POWER | [Power.v1_7_1.json](https://github.com/DMTF/Redfish-Publications/blob/main/json-schema/Power.v1_7_1.json) | Power supply, voltage and power control identifiers. |

## Corrected discrepancies and associated tests

| Before | Corrected behavior | Reference / test |
|---|---|---|
| `#Collection` types without schema names | Named standard collections, including ComputerSystemCollection, ManagerCollection and ChassisCollection | HPE-SYSTEM; `test_documented_collection_and_sensor_contracts` |
| Resources and sensors missing required identity | Added missing `Id`, `MemberId` and fragment links; deterministic synthetic identifiers | DMTF-SYSTEM/THERMAL/POWER; same test |
| Generic PATCH on hardware identity, health and readings | Read-only fields rejected. Boot limited to the three override properties; HPE HostName and media also supported | HPE-SYSTEM, DMTF-SYSTEM; `test_readonly_fields_and_mock_overrides_are_separate` |
| HPE BootOrder directly writable | Rejection of this documented read-only property | HPE-SYSTEM; `test_hpe_documented_success_and_boot_readonly_contract` |
| All resets returned 204 | Dell: 204; HPE: 200 with an iLO success message; Supermicro: 200 with a Success envelope | DELL-POWER, HPE-CLIENT, SMC-GUIDE; reset and envelope tests |
| HPE PATCH returned the resource | iLO ExtendedInfo envelope containing a Success MessageId; state available through GET | HPE-RESP; HPE contract test |
| A simple On on an already powered-on system consumed the Once boot override | Consumed during a simulated boot or reboot | HPE-SYSTEM; `test_boot_once_is_consumed_only_on_boot` |
| HPE exposed only OEM media actions | Added standard actions; Inserted and WriteProtected are honored | HPE-MEDIA; `test_hpe_standard_media_parameters_and_patch` |
| Supermicro SEL placed under Managers | Hardware log under Systems/1/LogServices/Log1, with links and members moved. Empty maintenance collection under Managers | SMC-GUIDE; `test_supermicro_sel_and_success_envelope` |
| Standard Supermicro media with a link to a missing OEM VM1 | Removed the hybrid link and advertised standard actions. Actions remain explicitly unsimulated (501), as asynchronous processing is not implemented | SMC-VM, SMC-GUIDE; same test |

Tests also traverse all `@odata.id` links in the three active profiles.
Errors now include `@Message.ExtendedInfo`. The message set remains a subset
and does not reproduce complete firmware registries.

## Unverified or incomplete areas

- **No captures from real hardware** have been compared. Serial numbers,
  part numbers, firmware versions, readings, states and configurations are synthetic.
- CPU/DIMM inventories have been completed with synthetic configurations
  supported by vendor documents: Dell 2 × 6338 / 16 × 32 GiB,
  HPE 2 × 4314 / 16 × 16 GiB, Supermicro 2 × 6230 / 12 × 16 GiB.
  Totals, speeds and slots are tested; empty slots and detailed SPD/OEM data
  are not exposed. See [inventory sources and choices](inventory.md).
- Schema versions and all OEM properties have not been validated field by
  field. These are targeted contract tests, not a full DMTF conformance
  validator run. Nested storage schemas, jobs and aggregate health states
  in particular still need comparison with captures.
- Power transitions are instantaneous and reproduce neither hardware delays
  nor OS failures. Manager.Reset does not simulate network downtime. System
  shutdown simulates 11–13 W standby power (12 W in static mode) and makes
  host sensors unavailable; it does not claim to measure these models'
  actual standby consumption.
- Firmware updates and asynchronous Supermicro mounting remain unimplemented.
  The available task sequences are independent test scenarios.
- Supported writes do not reproduce all hardware rules for deferred changes.
  There is no complete BIOS settings API: only the BIOS version and available
  boot properties are exposed.
- Provisioning, on-demand task creation, push events and disk persistence
  are not implemented.
- Mock authentication is independent of JSON accounts. Roles, licenses,
  access policies, message registries and schemas served by the BMC are incomplete.
- Lenovo and iLO 4 remain optional regression profiles, outside this vendor
  audit. The missing iLO 4 UpdateService link remains a known limitation.

To validate against a real fleet, capture responses for each targeted
model/firmware pair, retain their provenance, then compare URIs, types,
options, permissions and action effects. Passing local tests is not proof
of compatibility with every server from a vendor.
