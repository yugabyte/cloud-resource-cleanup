# Filter variants catalogue

Rung R1b. Read from the code on `refactor/python-agentic` (no behaviour change since
`main`). This is the spec the wiring rungs follow: `crc/core/policy.py` has one function per
variant named here. Citations are `file:line` of the current legacy modules.

Legend. Spec = a dict `key -> list of values`; "match" for a key is
`key in tags and (not values or tags[key] in values)`, so an **empty value list means "key
present"** and values are compared **exactly and case sensitively** (unless stated).
"any" = at least one spec key matches (`any(...)`, empty spec is False). "all" = every spec
key matches (`all(...)`). Policy names in `code` are in `crc/core/policy.py`.

Not covered: server-side API filters other than tags/states (regions, zones, priority),
CloudWatch detach logic of AWS disk, and delete order. Those stay in the cleaners.

## 1. How tags are read

| Cloud | Source | Shape | Notes |
|-------|--------|-------|-------|
| AWS | `Tags` | list of `{Key, Value}` | KMS: `list_resource_tags` gives `{TagKey, TagValue}` (`kms.py:131-136`) |
| Azure | `.tags` | dict or `None` | `None` for untagged |
| GCP | `.labels` | protobuf map (ScalarMapContainer) | behaves like a dict, empty when unlabelled |
| OCI | `.freeform_tags` | dict or `None` | `or {}` used everywhere |

`normalize_tags` turns all of these into `dict[str, str]` (list entries without a key are
dropped, a missing value becomes `""`, duplicate keys keep the last).

## 2. AWS

| Module | filter_tags | exception_tags / notags | Untagged | Regex | States | Age |
|--------|-------------|-------------------------|----------|-------|--------|-----|
| `aws/disk.py` | client side `filter_tags_glob_all_keys`: every key present; non-empty values are `fnmatchcase` globs, any pattern (`:210-221`). Also server side `status=available` plus `tag:key` or `tag-key` for an empty list (`:273-280`) | exception: any key (`:251-258`). notags: all keys, skip (`:260-265`). Applied after the filter match, inside `_should_skip` (`:235-271`) | skipped unless `notags` is set (`:379-383`, docstring `:51-52`) | name_regex: `re.search` any on the `Name` tag or the volume id, empty list matches (`:223-226`, `:386`). exception_regex: `re.search` any on the same name, empty list is False (`:228-233`, `:267`). Compiled in `__init__`, bad pattern raises `ValueError` (`:135-142`) | `State == "available"`, no `Attachments`, not `MultiAttachEnabled` (`:370-377`) | `get_retention_age` (fail open) then own validation, floor compare ignoring `MAX_AGE` (`:400-424`, `:436-452`, `:426-434`); `detach_age` via CloudWatch |
| `aws/elastic_ips.py` (lines cite `main`, frozen in `tests/legacy/aws_elastic_ips.txt`; since R4b it filters through `policy.filter_tags_any_key` and `policy.aws_eip_should_skip`, one tag at a time; the notags `TypeError` of BUG-011 is kept in the cleaner) | any key (`:132-139`); no filter includes every tagged EIP (`:129-131`) | exception: any key, returns skip at first hit (`:93-101`). notags: **never skips**, the accumulator starts False and is only and-ed (`:91-109`) | EIPs without a `Tags` key are skipped (`:125`); only EIPs without `NetworkInterfaceId` (`:125`) | none | unassociated only (`:125`) | none |
| `aws/keypairs.py` (lines cite `main`, frozen in `tests/legacy/aws_keypairs.txt`; since R4a it filters through `policy.name_regex_search_matches` and `policy.exception_regex_search_matches`) | none | none | n/a | name_regex: `re.search` any, empty matches (`:112-114`). exception_regex: `re.search` any, empty is False (`:115-117`). Invalid regex raises `re.error` at use (`:113`) | none | `is_old(self.age)` only, no custom age tag (`:120-124`) |
| `aws/kms.py` (lines cite `main`, frozen in `tests/legacy/aws_kms.txt`; since R4c it filters through `policy.filter_tags_exact_pairs` (one tag at a time, so a repeated `TagKey` keeps every value as the set on `main` does), `policy.exception_tags_any` and `policy.effective_age` with `crc.core.age`) | **all (key, value) pairs** present: set of `(k, v)` for every `v` in every list must be a subset of the key's tags (`:137-145`). A key with an empty list adds no pair, so it does not require the key; a key with two values never matches | exception: any key (`:85-99`, called `:147`), but `_should_skip_kms` indexes `tag["Key"]`/`tag["Value"]` (`:90-91`) on `{TagKey, TagValue}` dicts so any tagged key raises `KeyError` into `:181-183` when `exception_tags` is set. No notags | not skipped (no tag requirement without a filter) | none | `KeyState == "Enabled"`, description contains `kms_key_description` (`:161-163`), and `"default"` key policy `Statement` has `Principal.AWS == kms_user` (`:170-180`) | `get_retention_age` + `is_old(retention_age or age)` (`:155-169`), but `get_retention_age` looks up `"Key"` on `{TagKey, TagValue}` so custom age tags never match. Whole loop body in a bare `except:` that skips the key (`:181-183`) |
| `aws/snapshot.py` | none | exception: any key (`:28-37`). No notags | not protected, deleted when old (`:29`) | none | skipped when used by an AMI (`:78-80`) | cutoff `now - age_days`, kept when `StartTime > cutoff` (`:59`, `:72-73`); no custom age tag, no `is_old` |
| `aws/spot_instance_requests.py` | client side `filter_tags_glob_all_keys` (`:190-203`, applied `:148`), plus server `tag:key` filters (`:109-115`) | exception: any key (`:215-219`). notags: all keys, skip (`:222-226`). Same function for leftover volumes (`:250-254`) | skipped always, also for cancelled requests (`:135-139`, `:746-747`); cancelled without `InstanceId` skipped (`:141-144`) | none | request `State == "cancelled"` is handled specially; `Type` lowercased, default `one-time` (`:153`) | `validated_retention_age` (fail closed: bad tag keeps the resource) + `is_old` (`:155-172`, `:308-326`) |
| `aws/vm.py` | server side only: `instance-state-name` plus `tag:key` filters, EC2 semantics (`:174-185`) | exception: any key, notags: all keys, both via `_should_skip_instance` (`:255-280`) | skipped when the `Tags` key is absent (`:200-201`); must have a `Name` tag, else skipped (`:205-210`, `:290-293`) | none | `["running"]` (`:33`) via server filter | `get_retention_age` + `is_old` from the NIC attach time (`:218-235`); any exception skips the instance (`:241-244`) |
| `aws/vpc.py` | server side only `tag:key` filters (`:86-92`) | `_should_skip_vpc` same any/all logic as VM (`:94-127`) but it is called with the boto3 Vpc object, not a tag list (`:167`) | none | none | none | none |

AWS notes.

- **Sensitive yb_task** exists only in `aws/disk.py` and `aws/spot_instance_requests.py`.
  Both use the same value test `is_sensitive_yb_task` (`disk.py:180-192`,
  `spot_instance_requests.py:229-241`): casefold + strip; protected set
  `{infrastructure, infra, ipsec, vpn, prod, production}` (`disk.py:67-74`,
  `spot_instance_requests.py:67-74`); true on the whole value, on any token after splitting on
  `[-_\s/]+`, or on a `<word>-` / `<word>_` prefix. Differences: the disk finds the key by
  folding case and `-` to `_` and judges only the first such key (`disk.py:173-178`, `:236`,
  `aws_disk_has_sensitive_yb_task`); the spot module accepts exactly `yb_task` and `yb-task`
  (case sensitive) and either being sensitive protects (`spot_instance_requests.py:243-248`,
  `aws_spot_has_sensitive_yb_task`). The spot module applies it **only to leftover volumes**
  (`:250-254`, `:294`, `:733`), never to the spot request's own tags.
- **Kubernetes** skip exists only in the disk: keys starting `kubernetes.io/` or
  `ebs.csi.aws.com/`, or `KubernetesCluster` / `CSIVolumeName`, skip the volume unless
  `filter_tags` names such a key (`disk.py:194-208`, `:244-249`).
- VPC: `delete` records every VPC returned by the server filter before the skip check
  (`vpc.py:159-161`), and a dry run stops there (`:163-164`).
- Elastic IP and VPC state no state list; `vpc.py` and `elastic_ips.py` use no age.

## 3. Azure

| Module | filter_tags | exception_tags / notags | Untagged | Regex | States | Age |
|--------|-------------|-------------------------|----------|-------|--------|-----|
| `azu/disk.py` | **any key** (`:83-89`) | exception: any key, only when tags truthy (`:92-99`). notags: all keys, only when tags truthy (`:102-109`) | with a filter: not matched (`disk.tags and ...`); without a filter: included (`:83-89`) | none | `disk_state in state` for `state in ["Unattached"]`: **the disk state is a substring of the allowed string** (`:16`, `:124-126`), so `"attached"` also passes | `get_retention_age` + `is_old(retention_age or age)` (`:114-122`) |
| `azu/ip.py` | any key (`:62-68`) | exception: any key (`:70-73`), notags: all keys (`:75-78`), **no `None` guard**: `key in ip.tags` raises `TypeError` for untagged IPs when exception_tags/notags is set | with a filter: not matched; without: included | none | `ip_configuration is None` (`:84`) | none |
| `azu/nic.py` | none | none | n/a | name_regex: **plain substring** (`in`), any, empty gives False and the cleaner returns early (`:63-65`, `:96`). exception_regex: plain substring, any, empty False (`:97-101`) | `nic.virtual_machine` falsy (`:85`) | none |
| `azu/vm.py` | **all keys** (`:217-221`) | exception: any key (`:232-238`), notags: all keys (`:239-244`) | skipped (`:211-212`) | none | status text from `instance_view().statuses[1].display_status`; **an allowed state must be a substring of it** (`:149`, `:255-260`), default `["running"]` (`:21`) | `get_retention_age` + `is_old` (`:141-145`) |
| `azu/spot_vm.py` | all keys (`:108-121`) | exception any / notags all (`:91-106`); `_should_skip_instance` returns False for untagged VMs but the filter step rejects them | skipped in `_should_include_by_tags` (`:114-115`) | none | spot priority (`:73-77`, lowercased compare); power state exact member of `("VM stopped", "VM deallocated")` (`:17-18`, `:366-373`) | `validated_retention_age` (fail closed) + `is_old`; missing `time_created` skips (`:378-395`) |
| leftover Spot disks | none | exception any / notags all, evaluated on `disk.tags or {}` and only when tags are truthy (`:244-261`) | included | none | n/a | none |

`azu/vm_delete_helpers.py` has no filtering.

## 4. GCP

GCP calls them labels; the flags are the same (`--filter_labels`, `--exception_labels`,
`--notags`).

| Module | filter | exception / notags | Untagged (no labels) | Regex | States | Age |
|--------|--------|--------------------|----------------------|-------|--------|-----|
| `gcp/disk.py` | labels, **any key** (`:136-141`) | exception_labels: any key (`:155-161`). **notags is stored (`:80`) but never used.** `_should_skip_disk` returns `None` when not skipped | with a filter: not matched; without: included | exception_regex: `re.match` (anchored at start), any, on the disk name (`:150-153`). name_regex stored (`:81`), **never used** | `disk.users` empty (`:184-185`); `last_detach_timestamp` required (`:190-191`); `["Unattached"]` default (`:36`, `:165`) is never applied | `get_retention_age` + `is_old(retention_age or detach_age)` from `last_detach_timestamp` (`:117-124`); `age` stored (`:77`), **never used** |
| `gcp/ip.py` | filter_regex: **plain substring**, any, empty matches (`:87-89`) | exception_regex: `exception_regex and not any(sub in name)`, **inverted**: with the list set, names containing none of the entries are excluded (`:91-95`) | n/a (no labels) | substring, see left | `address.status == "RESERVED"` (`:83`) | none |
| `gcp/vm.py` | labels, **all keys** (`:234-239`) | exception_labels: any key (`:210-216`), notags: all keys (`:217-222`) | no labels with a filter: not matched; without: included | none | `instance.status in ["RUNNING"]` exact (`:30`, `:140`) | `get_retention_age` + `is_old(retention_age or age)` (`:250-257`) |
| `gcp/spot_vm.py` | labels, all keys (`:104-111`) | `_labels_protect`: exception any, notags all, False when no labels (`:72-85`) | included without a filter | none | exact `["RUNNING", "TERMINATED", "SUSPENDED"]` (`:25`, `:366`); spot = `preemptible` or provisioning model `SPOT` (`:62-70`) | `validated_retention_age` (fail closed) + `is_old` (`:113-126`) |

GCP GKE / MIG skip (Spot VM only): label `goog-gke-node` present, or instance metadata item
`created-by` whose value contains `instanceGroupManagers` (`gcp/spot_vm.py:90-102`, applied
`:370-371`), `gcp_is_managed_or_gke_node`. Leftover Spot disks use `_labels_protect` on
`disk.get("labels")` (`:301`).

## 5. OCI

| Module | filter | exception / notags | Untagged | Regex | States | Age |
|--------|--------|--------------------|----------|-------|--------|-----|
| `oci/disk.py` | freeform tags, **any key** (`:129-135`) | exception any (`:109-115`), notags all (`:117-122`) | with a filter: not matched; without: included | none | `lifecycle_state == "AVAILABLE"` and id not attached (`ATTACHED`/`ATTACHING`, `:17`, `:159-162`); block and boot volumes | `get_retention_age` + `is_old` (`:144-149`) |
| `oci/vm.py` | **all keys** (`:101-107`) | exception any (`:81-87`), notags all (`:88-94`) | included without a filter | none | `lifecycle_state in ["RUNNING"]` exact (`:21`, `:145`) | `get_retention_age` + `is_old` (`:116-121`) |
| `oci/spot_vm.py` | all keys (`:110-117`) | `_tags_protect`: exception any, notags all, False for empty tags (`:92-105`) | included without a filter | none | `["RUNNING", "STOPPED"]` exact (`:25`, `:262`); preemptible only (`:62-63`) | `validated_retention_age` (fail closed) + `is_old` (`:119-135`) |

OCI OKE skip (Spot VM only): case-insensitive substring `oke`, `instancepool`,
`instance_pool` or `instance-pool` in any freeform `key:value`, defined
`namespace:key:value` (non-dict namespace ignored) or instance metadata `key:value` (only
when metadata is a dict) (`oci/spot_vm.py:65-90`, applied `:266-267`),
`oci_is_managed_or_oke_node`. Leftover volumes: `_tags_protect(volume.freeform_tags)` (`:226`).

## 6. Variants

Grouping of behaviour that is identical, and every real difference.

**filter_tags**

- `filter_tags_any_key` (any key, empty passes): Azure disk, Azure IP, GCP disk, OCI disk, AWS
  Elastic IP. Untagged is excluded when a filter is set.
- `filter_tags_all_keys` (all keys, empty passes): Azure VM, Azure Spot VM, GCP VM, GCP Spot
  VM, OCI VM, OCI Spot VM.
- `filter_tags_glob_all_keys`: AWS disk, AWS spot requests (all keys plus `fnmatch` globs).
- `filter_tags_exact_pairs`: AWS KMS only (all pairs; multi-value never matches; empty list
  adds no constraint).
- Server side only (no client check): AWS VM, AWS VPC (`tag:key` EC2 filter, AND across keys,
  OR across values, glob wildcards by EC2). Wiring must keep the server filter.
- No filter at all: AWS keypair, AWS snapshot, Azure NIC, GCP IP.

**exception_tags / notags** (`should_skip_by_tags`)

- Identical (exception: any key, notags: all keys, skip if either): AWS disk, VM, VPC, spot
  requests; Azure disk, IP, VM, Spot VM; GCP VM, Spot VM; OCI disk, VM, Spot VM.
- Exception only (pass `{}` for notags): AWS KMS, AWS snapshot, GCP disk (notags stored, never
  applied). AWS Elastic IP: `aws_eip_should_skip`, notags never skips.
- Empty tags: exception is False, notags is False (a non-empty spec needs a key to match).
- Not identical in failure mode: Azure IP raises `TypeError` for `None` tags when
  exception_tags/notags are set; policy returns False instead. AWS VPC passes a resource
  object instead of tags (see open issues).

**Untagged** (`UntaggedMode`)

- `SKIP`: AWS spot requests, AWS VM (when `Tags` absent), AWS Elastic IP (when `Tags`
  absent), Azure VM, Azure Spot VM.
- `SKIP_UNLESS_NOTAGS`: AWS disk.
- `INCLUDE` (excluded only by an active filter): Azure disk, Azure IP, GCP disk, GCP VM, GCP
  Spot VM, OCI disk, OCI VM, OCI Spot VM, AWS KMS, AWS snapshot, AWS VPC.
- Note: AWS VM and Elastic IP test for an absent `Tags` key, so an empty list is not skipped;
  the other modules treat empty and absent alike. The wiring rung must evaluate presence
  before `normalize_tags` for these two.

**Regex / names**

- `re.search` any: AWS disk (name_regex and exception_regex, on the `Name` tag or id),
  AWS keypair. Empty name list matches, empty exception list does not.
- `re.match` any: GCP disk exception_regex only.
- Plain substring (not regex despite the flag name): GCP IP filter_regex and exception_regex
  (exception inverted), Azure NIC name_regex and exception_regex.
- GCP disk name_regex and notags are unused.
- Invalid patterns: AWS disk raises `ValueError` at construction (`compile_patterns`);
  keypair raises `re.error` while deleting; substring variants cannot fail.

**States**

- Exact membership: GCP VM, GCP Spot VM, OCI VM, OCI Spot VM, OCI disk (`AVAILABLE`), GCP
  IP, Azure Spot power state, AWS (server side `running` / `available`).
- Azure disk: disk state is a substring of an allowed string (`state_is_substring_of_any`).
- Azure VM: allowed string is a substring of the display status
  (`status_contains_any_allowed`). The two Azure directions are opposite.

**Case sensitivity.** All tag key and value comparisons and all regexes are case sensitive.
Folding only happens in: yb_task value (both AWS modules), yb_task key (AWS disk only),
OKE markers, Azure spot priority (`lower`), GCP provisioning model (`upper`), AWS spot
`Type` (`lower`). EC2 server filters are case sensitive.

**Sensitive yb_task**: AWS disk and AWS spot leftover volumes only (see section 2). Two key
lookups, one value test. Not present in Azure, GCP, OCI, KMS, keypair, snapshot, EIP, VPC, VM.

**Managed-node skips**: GKE label and MIG metadata (GCP Spot VM), OKE markers (OCI Spot VM),
Kubernetes CSI volumes (AWS disk). Nothing for AWS VM, Azure, or non-spot GCP/OCI.

**Age function**

- `is_old(self.age)` only: AWS keypair.
- `get_retention_age` (fail open) + `is_old(retention_age or age)`: AWS VM, AWS KMS, Azure
  disk, Azure VM, GCP VM, OCI disk, OCI VM; GCP disk with `detach_age` as the default.
- `validated_retention_age` (fail closed) + `is_old`: AWS spot requests, Azure Spot VM, GCP
  Spot VM, OCI Spot VM.
- AWS disk: `get_retention_age` plus its own validation and floor compare that ignores
  `MAX_AGE` (`aws_disk_creation_floor`, `is_older_than_floor`).
- AWS snapshot: day cutoff (`snapshot_old_enough`).
- None: AWS Elastic IP, AWS VPC, Azure IP, Azure NIC, GCP IP.

## 7. Gaps and open items for wiring rungs

1. AWS Elastic IP notags never skips; GCP IP exception_regex is inverted; GCP disk ignores
   notags, name_regex and age; KMS multi-value filter never matches and empty value list adds
   no constraint (plus KMS `_should_skip_kms` and `get_retention_age` index `Key`/`Value` on
   `{TagKey, TagValue}` dicts, so non-empty `exception_tags` on a tagged key raises `KeyError`
   into the bare `except:` and skips, and custom age tags never match); Azure disk state
   substring accepts `"attached"`. Policy functions preserve the tag/regex/state quirks; fix
   only in named behaviour rungs.
2. AWS VPC `_should_skip_vpc(vpc)` receives a resource (`vpc.py:167`). With exception_tags or
   notags set and a non-dry run this likely raises on iteration; not verified against live
   boto3. Policy cannot represent it; the VPC rung must decide (suggest: feed the VPC tags).
3. Azure IP raises on `None` tags with exception_tags/notags; policy returns False (skip
   nothing). Behaviour fix for the Azure rung to confirm.
4. EC2 `tag:key` server filter with an empty value list (AWS VM, VPC, spot) is passed through
   unchanged; its effect was not verified.
5. Fail-open custom age tag (`get_retention_age`) remains in 11 modules (SAF-008/009).
6. Untagged mode for AWS VM and Elastic IP depends on absence of the `Tags` key.
7. Sensitive yb_task and Kubernetes skips exist only in the AWS disk and spot leftover paths;
   other modules rely on exception_tags from Jenkins.
