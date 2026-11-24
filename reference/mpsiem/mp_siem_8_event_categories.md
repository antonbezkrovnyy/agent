# MaxPatrol SIEM 8.0: category.generic / category.high / category.low

Источник: официальное «Руководство разработчика» MaxPatrol SIEM 8.0 от 27.09.2023, приложение Д, таблица 281, страницы 446–454.

Источники, использованные при подготовке в исходном репозитории:

- `docs/maxpatrol_siem_develop_guide.pdf`;
- `docs/maxpatrol_siem_event_fields_utf8.txt`.

Структура записи:

```text
category.generic
└── category.high
    └── category.low
```

Если у `category.high` нет дочернего списка, официальная таблица не задаёт для него значения `category.low`. Написание, регистр, пробелы, `&` и `/` сохранены как в источнике.

## Access

- `Access`
  - `Authentication`
    - `Default Credentials`
    - `Host`
    - `Local`
    - `Remote`
    - `Service`
    - `Unknown Type`
  - `Authorization`
    - `Host`
    - `Network`
    - `Object`
    - `User`

## Accounting

- `Accounting`
  - `Network Accounting`
    - `Address Translation`
    - `Connections & Sessions`
    - `Firewall Rules Usage`
    - `Traffic`
  - `User Accounting`
    - `Administrative Privilege Use`
    - `Application & Component Launching`
    - `Command Execution`
    - `Data Usage`
    - `System Accounts Use`
    - `System Application Launching`

## Anomaly

- `Anomaly`
  - `Network Anomaly`
    - `Detection`
  - `System Anomaly`
  - `User Behavior Anomaly`

## Asset & Asset Group Management

- `Asset & Asset Group Management`
  - `Asset Group Management`
    - `Activation`
    - `Creation`
    - `Deactivation`
    - `Deletion`
    - `Modification`
  - `Asset Management`
    - `Domain Activation`
    - `Domain Creation`
    - `Domain Data Collection`
    - `Domain Deactivation`
    - `Domain Deletion`
    - `Host Activation`
    - `Host Creation`
    - `Host Data Collection`
    - `Host Deactivation`
    - `Host Deletion`
    - `Hypervisor Activation`
    - `Hypervisor Creation`
    - `Hypervisor Data Collection`
    - `Hypervisor Deactivation`
    - `Hypervisor Deletion`
    - `Link Activation`
    - `Link Creation`
    - `Link Data Collection`
    - `Link Deactivation`
    - `Link Deletion`
    - `Network Activation`
    - `Network Creation`
    - `Network Data Collection`
    - `Network Deactivation`
    - `Network Deletion`
    - `Virtual Host Activation`
    - `Virtual Host Creation`
    - `Virtual Host Data Collection`
    - `Virtual Host Deactivation`
    - `Virtual Host Deletion`

## Attacks & Recon

- `Attacks & Recon`
  - `Attack`
    - `Bruteforce`
    - `Complex Attack`
    - `DDoS`
    - `DoS`
    - `HIPS Alert`
    - `Identity Theft`
    - `IDS/IPS Alert`
    - `Miscellaneous`
    - `Network Attack`
    - `Post Compromise`
    - `Potential Attack`
    - `Privilege Escalation`
    - `Spam Attack`
    - `Vulnerability Exploitation`
  - `Recon`
    - `Crawling/Dictionary Bruteforce`
    - `Enumeration`
    - `Fingerprinting`
    - `Network Scan`
    - `OS Discovery`
    - `Port Discovery`
    - `Service Discovery`

## Business Continuity

- `Business Continuity`
  - `Backup/Restore`
    - `Backup Jobs`
    - `Backup Schedule`
    - `Restore Jobs`
  - `Cluster Nodes`
    - `Connection`
    - `Creation`
    - `Deletion`
    - `Replication`
  - `Replication`
    - `Data`
  - `Heartbeat`
  - `Settings`

## Compliance

- `Compliance`
  - `Policy Violation`
    - `Application Policy Violation`
    - `AV Policy Violation`
    - `Confidentiality Policy Violation`
    - `Database Policy Violation`
    - `IM Policy Violation`
    - `IP Access Policy Violation`
    - `Mail Policy Violation`
    - `P2P Policy Violation`
    - `Remote Access Policy Violation`
    - `System Update Policy Violation`
    - `Web Policy Violation`

## Configuration Management

- `Configuration Management`
  - `Network Configuration`
    - `Access Rule Modification`
    - `Interface Modification`
    - `Link Modification`
    - `Port Detection`
    - `Port State Change`
    - `Routing Changes`
  - `Rights Management`
    - `Critical Privileges`
    - `Group Assignment`
    - `Group Creation`
    - `Group Deletion`
    - `Group Modification`
    - `Object Rights Modification`
    - `Rights Assignment`
    - `Rights Revocation`
    - `Role Assignment`
    - `Role Creation`
    - `Role Deletion`
    - `Role Modification`
    - `Role Revocation`
    - `User Membership Modification`
  - `System Configuration Management`
    - `Application Object Modification`
    - `Application Settings Modification`
    - `Network Service Detection`
    - `Network Service Modification`
    - `Network Service Resumption`
    - `Network Service Stoppage`
    - `OS Service Detection`
    - `OS Service Modification`
    - `OS Service Resumption`
    - `OS Service Stoppage`
    - `OS Settings Modification`
    - `Security Settings Modification`
  - `User Management`
    - `Creation`
    - `Deletion`
    - `Detection`
    - `External User Connection`
    - `Locking`
    - `Modification`
    - `Unlocking`

## Hacktool

- `Hacktool`
  - `Bruteforcer`
    - `Deletion`
    - `Detection`
    - `Exploitation`
    - `Installation`
  - `DoS`
  - `Encryption`
  - `Enumeration`
  - `Exploit Kit`
  - `Fingerprint`
  - `Miscellaneous`
  - `MITM`
  - `Remote Shell`
  - `Scanner`
  - `Shellcode`
  - `Sniffer`
  - `Spoofing`
  - `Tunnel`
  - `Web Scanner`

## Hardware Management

- `Hardware Management`
  - `CPU Management`
    - `CPU Detection`
    - `CPU Resources Change`
  - `External Device Management`
    - `Bluetooth pairing`
    - `Mobile device`
    - `Printer`
    - `Scanner`
    - `USB Device`
  - `External Storage Management`
    - `External Disk Detection`
    - `External Disk Mounting`
    - `External Disk Unmounting`
    - `SAN/NAS Detection`
    - `SAN/NAS Mounting`
    - `SAN/NAS Unmounting`
  - `Hardware inventory`
    - `Hardware Configuration Errors`
    - `Hardware Detection`
    - `Hardware Installation`
    - `Hardware Removal`
  - `Memory Management`
    - `Memory Resizing`
  - `Storage Management`
    - `Local Storage`
    - `Network Storage`

## Incident Management

- `Incident Management`
  - `Incident`
    - `Creation`
    - `Deletion`
    - `Detection`
    - `Modification`
    - `Remediation`

## Information Management

- `Information Management`
  - `Data Loss`
    - `Accidental`
    - `Deliberate`
  - `Data Modification`
    - `By System`
    - `By User`
  - `Information Access Control`
    - `Assignment`
    - `Object Access`
    - `Settings`
  - `Information Labeling`
    - `Label Assignment`
    - `Label Management`
  - `Information Leak`
    - `Confidential Information`
    - `Critical Information`
  - `Leakage Channel`
    - `External Drive`
    - `Mail`
    - `Mobile Device`
    - `Printer`
    - `Web`

## Malware

- `Malware`
  - `Backdoor`
    - `Curing`
    - `Detection`
    - `Epidemic`
    - `Mitigation`
  - `Bootkit`
  - `Botnet`
  - `Miscellaneous`
  - `Rootkit`
  - `Trojan`
  - `Virus`
  - `Worm`

## Monitoring

- `Monitoring`
  - `Errors`
    - `Application`
    - `Hardware`
    - `Network`
    - `System`
  - `Network Monitoring`
    - `Interface State`
    - `Performance`
  - `System Monitoring`
    - `Notification`
    - `Performance`
    - `Processes`
    - `System State`

## Network Interaction

- `Network Interaction`
  - `Network Interaction`
    - `Application`
    - `Database`
    - `File transfer`
    - `Mail transfer`
    - `Remote Management`
    - `Suspicious`
    - `Unknown`
    - `VPN`
    - `Web`

## Software Management

- `Software Management`
  - `Operating System Management`
    - `Deletion`
    - `Detection`
    - `Installation`
    - `Update`
  - `Patch Management`
    - `Deletion`
    - `Installation`
  - `Security Software Management`
    - `Deletion`
    - `Detection`
    - `Installation`
    - `Update`
  - `Software Inventory`
    - `Deletion`
    - `Detection`
    - `Installation`
    - `Update`

## Vulnerability Management

- `Vulnerability Management`
  - `Vulnerability`
    - `Knowledge Base Updates`
    - `Potential Exploit Detection`
    - `Vulnerability Detection`
    - `Vulnerability Exception`
    - `Vulnerability Remediation`
