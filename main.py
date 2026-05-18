import argparse
import asyncio
import logging
import sys
import os

from automation_server_client import (
    AutomationServer,
    Workqueue,
    WorkItemError,
    Credential,
    WorkItemStatus,
)
from datetime import datetime, timedelta, timezone
from kmd_nexus_client import NexusClientManager
from kmd_nexus_client.tree_helpers import filter_by_path
from nexus_database_client import NexusDatabaseClient
from odk_tools.tracking import Tracker
from process.config import get_excel_mapping, load_excel_mapping

nexus: NexusClientManager
nexus_database_client: NexusDatabaseClient
tracker: Tracker

proces_navn = "Påmindelse om afregning af indsatser"


def opret_opgave_på_indsats(indsats_reference: dict):
    regler = get_excel_mapping()
    irrelevante_leverandører = regler.get("Irrelevante leverandører", [])
    paragraffer = {
        item.split(" | ")[0]: item.split(" | ")[1]
        for item in regler.get("Paragraffer til Løn og Personale", [])
    }
    indsats = nexus.hent_fra_reference(indsats_reference)
    opgave_historik = nexus.opgaver.hent_opgave_historik(indsats)
    opgave_modtager = "Regnskab BSF"

    # check irrelevante leverandører
    elementer = nexus.indsatser.hent_indsats_elementer(indsats)
    supplier_obj = (elementer or {}).get("supplier", {})
    nested_supplier = (supplier_obj or {}).get("supplier", {})
    leverandør = (nested_supplier or {}).get("name", None)

    if leverandør is None:
        return  # Ingen leverandør, spring oprettelse over

    if leverandør in irrelevante_leverandører:
        return  # Irrelevant leverandør, spring oprettelse over

    if opgave_historik is not None:
        for opgave in opgave_historik:
            if opgave["type"]["name"] == "Økonomi/Regnskab" and (
                opgave["lastStateChangeDate"] > indsats_reference["date"]
                or opgave["workflowState"]["name"] == "Aktiv"
            ):
                return  # Opgave oprettet inden for de sidste 2 dage, spring oprettelse over

    # Check paragraf ift. opgavemodtager
    paragraf = elementer.get("paragraph", {}).get("paragraph", {})

    if paragraf is not None and paragraf["name"] in paragraffer.keys():
        # check if paragraf.name exist in paragraffer dict and if the value matches paragraf.section
        value = paragraffer.get(paragraf["name"], None)
        if value == paragraf["section"]:
            opgave_modtager = "Løn og Personale 1"

    nexus.opgaver.opret_opgave(
        objekt=indsats,
        opgave_type="Økonomi/Regnskab",
        titel="Økonomi/Regnskab",
        ansvarlig_organisation=opgave_modtager,
        start_dato=datetime.now().date(),
        forfald_dato=datetime.now().date(),
        beskrivelse="Håndtering af indsatser til økonomi",
    )

    tracker.track_task(proces_navn)


async def populate_queue(workqueue: Workqueue):
    regler = get_excel_mapping()

    for org in regler["Organisation"]:
        borgere = nexus_database_client.hent_borgere_med_ændrede_indsatser(
            org, 2, regler["Status på indsats"]
        )

        for borger in borgere:
            eksisterende_kødata = workqueue.get_item_by_reference(
                borger["Cpr"], WorkItemStatus.NEW
            )

            if len(eksisterende_kødata) > 0:
                continue

            data = {
                "Cpr": borger["Cpr"],
            }

            workqueue.add_item(data, reference=borger["Cpr"])


async def process_workqueue(workqueue: Workqueue):
    regler = get_excel_mapping()
    logger = logging.getLogger(__name__)

    for item in workqueue:
        with item:
            data = item.data  # Item data deserialized from json as dict

            try:
                borger = nexus.borgere.hent_borger(data["Cpr"])

                if borger is None:
                    raise WorkItemError(
                        f"Borger med CPR {data['Cpr']} ikke fundet i Nexus"
                    )

                visning = nexus.borgere.hent_visning(borger)

                if visning is None:
                    raise WorkItemError(
                        f"Visning for borger med CPR {data['Cpr']} ikke fundet i Nexus"
                    )

                referencer = nexus.borgere.hent_referencer(visning)

                if referencer is None:
                    raise WorkItemError(
                        f"Referencer for borger med CPR {data['Cpr']} ikke fundet i Nexus"
                    )

                forløbs_referencer = filter_by_path(
                    referencer,
                    "/Børn og Unge Grundforløb/patientPathwayReference",
                    active_pathways_only=False,
                )

                for forløb in forløbs_referencer:
                    indsats_referencer = filter_by_path(
                        referencer,
                        f"/Børn og Unge Grundforløb/{forløb['name']}/Indsatser/*",
                        active_pathways_only=False,
                    )

                    for indsats in indsats_referencer:
                        indsats_date = datetime.fromisoformat(
                            indsats["date"].replace("+0000", "+00:00")
                        )
                        if indsats["workflowState"]["name"] in regler[
                            "Status på indsats"
                        ] and indsats_date >= datetime.now(timezone.utc) - timedelta(
                            days=2
                        ):
                            opret_opgave_på_indsats(indsats)

            except WorkItemError as e:
                # A WorkItemError represents a soft error that indicates the item should be passed to manual processing or a business logic fault
                logger.error(f"Error processing item: {data}. Error: {e}")
                item.fail(str(e))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    ats = AutomationServer.from_environment()
    workqueue = ats.workqueue()

    nexus_credential = Credential.get_credential("KMD Nexus - produktion")
    nexus_database_credential = Credential.get_credential("KMD Nexus - database")
    tracking_credential = Credential.get_credential("Odense SQL Server")

    nexus = NexusClientManager(
        client_id=nexus_credential.username,
        client_secret=nexus_credential.password,
        instance=nexus_credential.data["instance"],
        timeout=120,
    )

    nexus_database_client = NexusDatabaseClient(
        host=nexus_database_credential.data["hostname"],
        port=nexus_database_credential.data["port"],
        user=nexus_database_credential.username,
        password=nexus_database_credential.password,
        database=nexus_database_credential.data["database_name"],
    )

    tracker = Tracker(
        username=tracking_credential.username, password=tracking_credential.password
    )

    # Parse command line arguments
    parser = argparse.ArgumentParser(description=proces_navn)
    parser.add_argument(
        "--excel-file",
        default="./Regelsæt.xlsx",
        help="Path to the Excel file containing mapping data (default: ./Regelsæt.xlsx)",
    )
    parser.add_argument(
        "--queue",
        action="store_true",
        help="Populate the queue with test data and exit",
    )
    args = parser.parse_args()

    # Validate Excel file exists
    if not os.path.isfile(args.excel_file):
        raise FileNotFoundError(f"Excel file not found: {args.excel_file}")

    # Load excel mapping data once on startup
    load_excel_mapping(args.excel_file)

    # Queue management
    if "--queue" in sys.argv:
        workqueue.clear_workqueue(WorkItemStatus.NEW)
        asyncio.run(populate_queue(workqueue))
        exit(0)

    # Process workqueue
    asyncio.run(process_workqueue(workqueue))
