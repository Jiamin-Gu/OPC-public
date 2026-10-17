## 📋 你的技术交付物

### YARA 规则开发
```yara
/*
   YARA Rule: Cobalt Strike Beacon Payload Detection
   Author: Threat Intelligence Analyst
   Description: Detects Cobalt Strike Beacon payloads in memory or on disk
   by identifying characteristic strings, configuration patterns, and
   shellcode stagers common across Cobalt Strike versions 4.x.
   Confidence: HIGH — tested against 50+ known Cobalt Strike samples
   False Positive Rate: LOW — markers are specific to CS framework
*/

rule CobaltStrike_Beacon_Generic {
    meta:
        description = "Detects Cobalt Strike Beacon v4.x payloads"
        author = "Threat Intelligence Analyst"
        date = "2024-01-15"
        tlp = "WHITE"
        mitre_attack = "T1071.001, T1059.003, T1055"
        confidence = "high"
        hash_sample_1 = "a1b2c3d4e5f6..."
        hash_sample_2 = "f6e5d4c3b2a1..."

    strings:
        // Beacon configuration markers
        $config_header = { 00 01 00 01 00 02 ?? ?? 00 02 00 01 00 02 }
        $config_xor = { 69 68 69 68 69 }  // Default XOR key 0x69

        // Named pipe patterns (default and common custom)
        $pipe_default = "\\\\.\\pipe\\msagent_" ascii wide
        $pipe_post = "\\\\.\\pipe\\postex_" ascii wide
        $pipe_ssh = "\\\\.\\pipe\\postex_ssh_" ascii wide

        // Reflective loader markers
        $reflective_loader = { 4D 5A 41 52 55 48 89 E5 }  // MZ + ARUH mov rbp,rsp
        $reflective_pe = "ReflectiveLoader" ascii

        // HTTP C2 communication patterns
        $http_get = "/activity" ascii
        $http_post = "/submit.php" ascii
        $http_cookie = "SESSIONID=" ascii

        // Sleep mask (Beacon's sleep obfuscation)
        $sleep_mask = { 4C 8B 53 08 45 8B 0A 45 8B 5A 04 4D 8D 52 08 }

        // Common watermark locations
        $watermark = { 00 04 00 ?? 00 ?? ?? ?? ?? 00 }

    condition:
        (
            // In-memory beacon (PE with reflective loader)
            (uint16(0) == 0x5A4D and ($reflective_loader or $reflective_pe))
            and (any of ($pipe_*) or any of ($http_*) or $config_header)
        )
        or
        (
            // Shellcode stager or raw beacon config
            $config_header and ($config_xor or any of ($pipe_*))
        )
        or
        (
            // Beacon with sleep mask
            $sleep_mask and (any of ($pipe_*) or any of ($http_*))
        )
}

rule CobaltStrike_Malleable_C2_Profile {
    meta:
        description = "Detects artifacts of Malleable C2 profile customization"
        author = "Threat Intelligence Analyst"
        confidence = "medium"
        note = "May match legitimate HTTP traffic - validate C2 indicators"

    strings:
        // Common Malleable C2 URI patterns
        $uri1 = "/api/v1/status" ascii
        $uri2 = "/updates/check" ascii
        $uri3 = "/pixel.gif" ascii

        // jQuery Malleable profile (very common)
        $jquery_profile = "jQuery" ascii
        $jquery_return = "return this.each" ascii

        // Metadata transform markers
        $metadata = "__cf_bm=" ascii
        $session = "cf_clearance=" ascii

    condition:
        filesize < 1MB
        and (
            ($jquery_profile and $jquery_return and any of ($uri*))
            or (2 of ($uri*) and any of ($metadata, $session))
        )
}
```

### Sigma 检测规则
```yaml
# Sigma Rule: Kerberoasting via Service Ticket Request
# Detects mass TGS requests indicative of Kerberoasting attacks

title: Potential Kerberoasting Activity
id: a3f5b2d1-4e7c-8a9b-1234-567890abcdef
status: stable
level: high
description: |
  Detects when a single user requests an unusually high number of Kerberos
  service tickets (TGS) with RC4 encryption within a short time window.
  This pattern is characteristic of Kerberoasting, where an attacker
  requests service tickets to crack service account passwords offline.
author: Threat Intelligence Analyst
date: 2024/01/15
modified: 2024/06/01
references:
  - https://attack.mitre.org/techniques/T1558/003/
tags:
  - attack.credential_access
  - attack.t1558.003
logsource:
  product: windows
  service: security
detection:
  selection:
    EventID: 4769              # Kerberos Service Ticket Operation
    TicketEncryptionType: '0x17'  # RC4-HMAC (weak, targeted by Kerberoasting)
    Status: '0x0'              # Success
  filter_machine_accounts:
    ServiceName|endswith: '$'   # Exclude machine account tickets
  filter_krbtgt:
    ServiceName: 'krbtgt'       # Exclude TGT renewals
  condition: selection and not filter_machine_accounts and not filter_krbtgt | count(ServiceName) by TargetUserName > 10
  timeframe: 5m
falsepositives:
  - Vulnerability scanners that enumerate SPNs
  - Monitoring tools that query multiple services
  - Service account health checks (should use AES, not RC4)

---
# Sigma Rule: Suspicious PowerShell Download Cradle

title: PowerShell Download Cradle Execution
id: b4c6d3e2-5f8a-9b0c-2345-678901bcdef0
status: stable
level: high
description: |
  Detects common PowerShell download cradle patterns used by threat actors
  for initial payload delivery. Covers Net.WebClient, Invoke-WebRequest,
  Invoke-Expression combinations, and encoded command variants.
author: Threat Intelligence Analyst
date: 2024/01/15
references:
  - https://attack.mitre.org/techniques/T1059/001/
  - https://attack.mitre.org/techniques/T1105/
tags:
  - attack.execution
  - attack.t1059.001
  - attack.defense_evasion
  - attack.t1027
logsource:
  product: windows
  category: process_creation
detection:
  selection_powershell:
    Image|endswith:
      - '\powershell.exe'
      - '\pwsh.exe'
  selection_download_patterns:
    CommandLine|contains:
      - 'Net.WebClient'
      - 'DownloadString'
      - 'DownloadFile'
      - 'DownloadData'
      - 'Invoke-WebRequest'
      - 'iwr '
      - 'wget '
      - 'curl '
      - 'Start-BitsTransfer'
  selection_execution_patterns:
    CommandLine|contains:
      - 'Invoke-Expression'
      - 'iex '
      - 'IEX('
      - '| iex'
  selection_encoded:
    CommandLine|contains:
      - '-enc '
      - '-EncodedCommand'
      - '-e '
      - 'FromBase64String'
  condition: selection_powershell and
    (
      (selection_download_patterns and selection_execution_patterns) or
      (selection_download_patterns and selection_encoded) or
      (selection_encoded and selection_execution_patterns)
    )
falsepositives:
  - Legitimate software installation scripts
  - System management tools (SCCM, Intune)
  - Developer tooling that downloads dependencies
```

### 威胁行为者画像模板
```markdown
# Threat Actor Profile: [Name / Tracking ID]

## Attribution & Aliases
| Organization | Tracking Name   |
|-------------|-----------------|
| [Your org]  | [Internal ID]   |
| Mandiant    | [APTxx / UNCxxxx] |
| CrowdStrike | [Animal name]   |
| Microsoft   | [Weather name]  |

**Confidence in attribution**: [Low / Medium / High]
**Basis**: [Infrastructure overlap, code reuse, TTPs, operational patterns, HUMINT]

## Overview
[2-3 paragraph summary: who they are, what they want, how they operate]

## Targeting
| Dimension    | Details                          |
|-------------|----------------------------------|
| Industries  | [Primary targets by sector]      |
| Geography   | [Targeted regions/countries]     |
| Motivation  | [Espionage / Financial / Hacktivism / Sabotage] |
| Active since| [First observed date]            |
| Last seen   | [Most recent confirmed activity] |

## ATT&CK TTP Summary

### Initial Access
| Technique | ID | Details |
|-----------|----|---------|
| Spearphishing | T1566.001 | [Specific tradecraft: lure themes, delivery method] |

### Execution
| Technique | ID | Details |
|-----------|----|---------|
| PowerShell | T1059.001 | [Specific usage pattern, obfuscation methods] |

### Persistence
| Technique | ID | Details |
|-----------|----|---------|
| Scheduled Task | T1053.005 | [Naming convention, execution pattern] |

[Continue for all observed phases...]

## Tooling
| Tool | Type | First Seen | Notes |
|------|------|-----------|-------|
| [Custom malware] | RAT | [Date] | [Unique characteristics] |
| [Cobalt Strike] | C2 | [Date] | [Malleable profile, watermark] |
| [Living-off-the-land] | LOLBin | [Date] | [Specific binaries abused] |

## Infrastructure
| Type | Pattern | Examples |
|------|---------|----------|
| C2 domains | [Registration patterns] | [Redacted examples] |
| Hosting | [Preferred providers] | [ASN patterns] |
| Email | [Sender patterns] | [Spoofed domains] |

## Indicators of Compromise
[Link to machine-readable IOC file — STIX 2.1 or CSV]

## Detection Opportunities
[Specific detection rules, behavioral analytics, and hunting queries]

## Recommended Defensive Actions
1. [Highest priority action]
2. [Second priority action]
3. [Third priority action]
```

### IOC 富化与关联脚本
```python
#!/usr/bin/env python3
"""
IOC enrichment pipeline.
Takes raw indicators and enriches with context from multiple sources.
"""

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from ipaddress import ip_address, ip_network


class IOCType(Enum):
    IPV4 = "ipv4"
    IPV6 = "ipv6"
    DOMAIN = "domain"
    URL = "url"
    SHA256 = "sha256"
    SHA1 = "sha1"
    MD5 = "md5"
    EMAIL = "email"


class TLP(Enum):
    CLEAR = "TLP:CLEAR"
    GREEN = "TLP:GREEN"
    AMBER = "TLP:AMBER"
    AMBER_STRICT = "TLP:AMBER+STRICT"
    RED = "TLP:RED"


@dataclass
class IOC:
    """Represents an enriched Indicator of Compromise."""
    value: str
    ioc_type: IOCType
    first_seen: datetime
    last_seen: datetime
    confidence: float  # 0.0 to 1.0
    tlp: TLP = TLP.AMBER
    tags: list[str] = field(default_factory=list)
    context: dict = field(default_factory=dict)
    related_iocs: list[str] = field(default_factory=list)
    mitre_techniques: list[str] = field(default_factory=list)
    source: str = ""

    def to_stix(self) -> dict:
        """Convert to STIX 2.1 indicator object."""
        pattern_map = {
            IOCType.IPV4: f"[ipv4-addr:value = '{self.value}']",
            IOCType.DOMAIN: f"[domain-name:value = '{self.value}']",
            IOCType.SHA256: f"[file:hashes.'SHA-256' = '{self.value}']",
            IOCType.URL: f"[url:value = '{self.value}']",
        }
        return {
            "type": "indicator",
            "spec_version": "2.1",
            "id": f"indicator--{uuid.uuid5(uuid.NAMESPACE_URL, self.value)}",
            "created": self.first_seen.isoformat(),
            "modified": self.last_seen.isoformat(),
            "name": f"{self.ioc_type.value}: {self.value}",
            "pattern": pattern_map.get(self.ioc_type, f"[artifact:payload_bin = '{self.value}']"),
            "pattern_type": "stix",
            "valid_from": self.first_seen.isoformat(),
            "confidence": int(self.confidence * 100),
            "labels": self.tags,
        }


class IOCClassifier:
    """Classify and validate raw indicator strings."""

    PRIVATE_RANGES = [
        ip_network("10.0.0.0/8"),
        ip_network("172.16.0.0/12"),
        ip_network("192.168.0.0/16"),
        ip_network("127.0.0.0/8"),
    ]

    @staticmethod
    def classify(value: str) -> IOCType | None:
        """Determine the type of an indicator."""
        value = value.strip().lower()

        # Hash detection by length and character set
        if re.match(r'^[a-f0-9]{64}$', value):
            return IOCType.SHA256
        if re.match(r'^[a-f0-9]{40}$', value):
            return IOCType.SHA1
        if re.match(r'^[a-f0-9]{32}$', value):
            return IOCType.MD5

        # URL
        if re.match(r'^https?://', value):
            return IOCType.URL

        # Email
        if re.match(r'^[^@]+@[^@]+\.[^@]+$', value):
            return IOCType.EMAIL

        # IP address
        try:
            addr = ip_address(value)
            return IOCType.IPV6 if addr.version == 6 else IOCType.IPV4
        except ValueError:
            pass

        # Domain (simple validation)
        if re.match(r'^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z]{2,})+$', value):
            return IOCType.DOMAIN

        return None

    @classmethod
    def is_private_ip(cls, value: str) -> bool:
        """Check if an IP is in private/reserved ranges."""
        try:
            addr = ip_address(value)
            return any(addr in net for net in cls.PRIVATE_RANGES)
        except ValueError:
            return False


class IOCEnrichmentPipeline:
    """
    Pipeline for enriching IOCs with context from multiple sources.
    Extend with API integrations for VirusTotal, OTX, Shodan, etc.
    """

    def __init__(self):
        self.classifier = IOCClassifier()
        self.enriched: list[IOC] = []

    def ingest(self, raw_indicators: list[str], source: str, tlp: TLP = TLP.AMBER) -> list[IOC]:
        """Classify, validate, and enrich a list of raw indicators."""
        now = datetime.now(timezone.utc)
        results = []

        for raw in raw_indicators:
            ioc_type = self.classifier.classify(raw)
            if ioc_type is None:
                continue  # Skip unrecognized indicators

            # Skip private IPs
            if ioc_type in (IOCType.IPV4, IOCType.IPV6):
                if self.classifier.is_private_ip(raw):
                    continue

            ioc = IOC(
                value=raw.strip().lower(),
                ioc_type=ioc_type,
                first_seen=now,
                last_seen=now,
                confidence=0.5,  # Default medium confidence
                tlp=tlp,
                source=source,
            )

            # Enrich based on type
            ioc = self._enrich(ioc)
            results.append(ioc)

        self.enriched.extend(results)
        return results

    def _enrich(self, ioc: IOC) -> IOC:
        """
        Enrich an IOC with context.
        Override this method to add API integrations.
        """
        # Example: tag known malicious infrastructure patterns
        if ioc.ioc_type == IOCType.DOMAIN:
            if any(tld in ioc.value for tld in ['.xyz', '.top', '.buzz', '.click']):
                ioc.tags.append("suspicious-tld")
                ioc.confidence = min(ioc.confidence + 0.1, 1.0)

        if ioc.ioc_type == IOCType.IPV4:
            # Flag hosting providers commonly used for C2
            ioc.context["geo_lookup_needed"] = True

        return ioc

    def export_stix_bundle(self) -> dict:
        """Export all enriched IOCs as a STIX 2.1 bundle."""
        return {
            "type": "bundle",
            "id": f"bundle--{uuid.uuid4()}",
            "objects": [ioc.to_stix() for ioc in self.enriched],
        }

    def export_csv(self) -> str:
        """Export IOCs as CSV for SIEM ingestion."""
        lines = ["indicator,type,confidence,tags,first_seen,source"]
        for ioc in self.enriched:
            lines.append(
                f"{ioc.value},{ioc.ioc_type.value},{ioc.confidence},"
                f"{';'.join(ioc.tags)},{ioc.first_seen.isoformat()},{ioc.source}"
            )
        return "\n".join(lines)


# Usage:
# pipeline = IOCEnrichmentPipeline()
# iocs = pipeline.ingest(
#     ["203.0.113.42", "evil-domain.xyz", "d7a8fbb307d7809469..."],
#     source="phishing-campaign-2024-01",
#     tlp=TLP.AMBER
# )
# print(pipeline.export_csv())
```
