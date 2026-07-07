"""Core sync engine. Stage 1 implements the Contacts flow end to end (Spec §9):
dedupe -> conflict detection -> approval routing -> before-state audit, with
Account-linkage guarding."""
