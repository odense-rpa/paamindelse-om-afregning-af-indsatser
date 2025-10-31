import argparse
import asyncio
import logging
import sys
import os

from automation_server_client import AutomationServer, Workqueue, WorkItemError, Credential
from nexus_database_client import NexusDatabaseClient
from kmd_nexus_client import NexusClientManager
from odk_tools.tracking import Tracker
from odk_tools.reporting import Reporter
from process.config import get_excel_mapping, load_excel_mapping

nexus: NexusClientManager
nexus_database_client: NexusDatabaseClient
tracker: Tracker
reporter: Reporter

proces_navn = "Påmindelse om afregning af indsatser"

async def populate_queue(workqueue: Workqueue):    
    regler = get_excel_mapping()
    
    for org in regler["Organisation"]:
        borgere = nexus_database_client.hent_borgere_med_ændrede_indsatser(org, 2, regler["Status på indsats"])

        for borger in borgere:
            eksisterende_kødata = workqueue.get_item_by_reference(borger["Cpr"])

            if len(eksisterende_kødata) > 0:
                continue

            data = {
                "Cpr": borger["Cpr"],
            }
            
            workqueue.add_item(data, reference=borger["Cpr"])

async def process_workqueue(workqueue: Workqueue):
    logger = logging.getLogger(__name__)

    logger.info("Hello from process workqueue!")

    for item in workqueue:
        with item:
            data = item.data  # Item data deserialized from json as dict
 
            try:
                # Hent borger
                # Hent referencer
                # Filtrer forløb
                # Filtrer indsatser på forløb
                # Check om hver indsats har status matchende regelsæt og om de er ændret indenfor de seneste 2 døgn
                # Saml collection af indsatser på borger der skal have en opgave
                # Loop collection:
                # Kontroller blacklisted leverandør
                # Check opgave historik
                # Check paragraf ift. opgavemodtager
                # Opret opgave på indsats til x
                pass
            except WorkItemError as e:
                # A WorkItemError represents a soft error that indicates the item should be passed to manual processing or a business logic fault
                logger.error(f"Error processing item: {data}. Error: {e}")
                item.fail(str(e))


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO        
    )

    ats = AutomationServer.from_environment()
    workqueue = ats.workqueue()

    nexus_credential = Credential.get_credential("KMD Nexus - produktion")
    nexus_database_credential = Credential.get_credential("KMD Nexus - database")    
    tracking_credential = Credential.get_credential("Odense SQL Server")
    reporting_credential = Credential.get_credential("RoboA")

    nexus = NexusClientManager(
        client_id=nexus_credential.username,
        client_secret=nexus_credential.password,
        instance=nexus_credential.data["instance"],
    )    
    
    nexus_database_client = NexusDatabaseClient(
        host = nexus_database_credential.data["hostname"],
        port = nexus_database_credential.data["port"],
        user = nexus_database_credential.username,
        password = nexus_database_credential.password,
        database = nexus_database_credential.data["database_name"],
    )

    tracker = Tracker(
        username=tracking_credential.username, 
        password=tracking_credential.password
    )

    reporter = Reporter(
        username=reporting_credential.username,
        password=reporting_credential.password
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


    # Initialize external systems for automation here..

    # Queue management
    if "--queue" in sys.argv:
        workqueue.clear_workqueue("new")
        asyncio.run(populate_queue(workqueue))
        exit(0)

    # Process workqueue
    asyncio.run(process_workqueue(workqueue))
