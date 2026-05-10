# NMD Contest Suite

## Software Suite for Administrating and Scoring the National Mountain Day Hamradio Contest



### Technology Stack

* Python3 with suitable web framework
* SQLite3
* Swisstopo Map
* Docker (deployment)



### Information about the National Mountain Day Contest (NMD)

* Website: https://nmd.uska.ch/
* Rules: See PDF



### Module Overview

#### Registration

Web frontend for potential participants to register for the next contest. There is currently a registration form online here: https://nmd.uska.ch/index.php/vorbereitung/anmeldung/

It is implemented in WordPress. A similar registration page should be implemented as part of the NMD Contest Suite and run in the same docker container as the other modules. It will later be integrated into wordpress (e.g. via iframe) and replace the current registration page. When registering, an account should be created and user/password sent to the participant by email.

#### 

#### Participant Portal

After the contest, the participants can either enter or upload the list of stations they worked during the contest (QSOs) via the participant portal. A dedicated log submission application has been created already earlier, it can be used as a template. An additional feature of the participant portal should be access to the scoring decisions, so the participant can review the scoring of his personal log and see where he may have lost points.





#### Scoring Module

The scoring module can start working as soon as the participants start submitting their logs. It matches QSOs between the logs and applies the scoring rules as defined by the contest rules. There exists a standalone web application written in TCL that can be used as a reference. The TCL implementation should be checked against the rules, modernized, and ported to python. The scoring frontend is used only by the contest administrator. It should perform the scoring as automated as possible, but let the administrator review the scoring decisions and make manual adjustments where needed. The scoring module also generates the ranking lists.



#### Administration Module

A dedicated web frontend to allow basic maintenance tasks, such as setting up a new contest (used annually), send email messages to all participants, archive previous contests, or access archived contest data.



### Detail Specifications

#### Registration

The registration form should provide a form similar to the existing one on https://nmd.uska.ch/index.php/vorbereitung/anmeldung/. The following fields are requested:

|**Field Name**|Type|Mandatory|
|-|-|-|
|Call Sign|valid HamRadio Callsign|yes|
|First Name|Text (UTF-8)|yes|
|Email|valid email address|yes|
|Multi Operator (Mehrmann Station)|Radio Button yes/no|yes|
|Station Chief (only if Multi Operator)|valid HamRadio Callsign|yes if Multi Operator == yes|
|Planned Station Location|Geo Coordinates|yes|
|Planned Station Altitude|Integer (meters ASL)|yes|
|Canton|2-Letter list / drop down of Swiss Cantons (26)|yes|
|Operation Mode|2 Checkbox for CW and/or SSB|yes (at least 1 must be selected)|
|Remarks|Text|No|



For the Station Location, the position of the station during the contest can be either provided by coordinates (support at least WGS84, CH1903, and CH1903+ format with automatic detection), or be selected from a map that is show above the registration form. The map should also show the position of stations that have already registered for the upcoming contest before. This allows the participant to avoid using already occupied locations.



Once the registration is submitted, an account should be created for the participant. Login name is the provided call sign (without /P postfix if it has been provided), password should be randomly generated. An email with a confirmation message, the URL for the participant portal, as well as the username/password is sent to the participant using the provided email message.



#### Participant Portal

The participant portal provides the following functionality for the participant:

* Managing the registration data: allows changing the following fields: Multi Operator, Station Chief, Planned Station Location, Planned Station Altitude, Canton, Operation Mode, Remarks. The Call Sign, First Name and Email address cannot be changed. The participant can also cancel the participation. After a confirmation, the registration and user account is then deleted.
* Entering or uploading QSO list and Station Data. Use the existing python application as reference. 
* Scoring page: Once the scoring is completed and has been published by the contest administrator, the participant can review the score and find out which connections were matched, and where he possibly made mistakes (e.g. mismatch between transmitted and received texts, invalid call signs etc). This can be a separate page, or integrated in the log submission page.





#### Scoring Module

Use the provided TCL code as a reference. Check it against the contest rules. In addition to the functionality of the TCL implementation, the following enhancements should be done:

* Accept up to 2 errors (wrong or missing character) when comparing sent and received message. This is not written in the rules, but an established practice and known by most participants. This check has previously done manually
* Automatically deduct duplicate connections (TCL application only shows warning, and administrator needs to manually invalidate connection)
* Find potentially wrong call signs: If the recipient of a message is not getting the sender call sign right, there will be no match in the logs. Such mistakes can be detected by matching by time stamp (including some margin) and the exchanged text. There will be no points given in this case, but it would be helpful as information to the participant to learn about the incorrect sender call sign.



#### Administration Module

The administration web application should provide the following functionality:

* On-Behalf registration: Manually register a station in the system that did not use the regular registration form. 
* Edit registration data: Allows editing registration data for all participants by the administrator
* On-Behalf log submission: Edit or upload a log on-behalf for a participant
* Close registration: No new registrations are possible, already registered stations can still change their registration data however
* Close log submissions: Don't accept any new uploads. Logs that were entered or uploaded but not submitted will be 'auto-submitted'.
* Email sending: Manually send a message (text field) to all registered participants
* Publish results: Creates the final ranking lists (one for SSB and one for CW). Enable the scoring page in the participant portal, so the participant can review their scoring.
* Setup new contest: Prepare the data base for a new contest. Archive the last contest and disable all participant user accounts.
* Backup data: Download complete database content
* Restore data: Upload database dump 





#### Additional Constraints

* A single data base should be used by all modules
* Data of previous contests should remain in the data base, however the modules are only using the most recent / currently active ones
* All persistent data should be stored in the data base, this allows easy restore of the data if the application needs to be migrated to a new server









