# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Purpose

Shows how to create a fully serverless REST API for a simple lending library
that is backed by AWS Lambda functions that call an Amazon Aurora database.
"""

import argparse
import logging
import os
from pprint import pprint
import random
import time
from urllib.parse import urljoin
import requests
import boto3
import yaml

from library_api.chalicelib.library_data import Storage

logger = logging.getLogger(__name__)

# Read YAML configuration
with open("config.yml", "r") as file:
    config = yaml.safe_load(file)


def find_api_url(stack_name):
    """
    Find the API URL from the AWS CloudFormation stack that was used to create
    the resources for this demo.

    :param stack_name: The name of the stack.
    :return: The endpoint URL found in the stack description.
    """
    cloudformation = boto3.resource("cloudformation")
    stack = cloudformation.Stack(name=stack_name)
    try:
        api_url = next(
            output["OutputValue"]
            for output in stack.outputs
            if output["OutputKey"] == "EndpointURL"
        )
        logging.info(
            "Found API URL in %s AWS CloudFormation stack: %s", stack_name, api_url
        )
    except StopIteration:
        logger.warning(
            "Couldn't find the REST URL for your API. Try running the following "
            "at the command prompt:\n"
            "\taws cloudformation describe-stacks --stack-name {stack_name} "
            "--query \"Stacks[0].Outputs[?OutputKey=='EndpointURL'].OutputValue\" "
            "--output text"
        )
    else:
        return api_url


def fill_db_tables(books_url, storage):
    """
    Fills the lending library database with example data.
    This demo uses data from the Internet Archive's Open Library,
    which can be found here:
        https://openlibrary.org

    :param books_url: The URL to the Open Library API.
    :param storage: The storage object that wraps calls to the Aurora database.
    :return The count of authors and the count of books added to the database.
    """
    logger.info("Getting book count from %s.", books_url)
    response = requests.get(f"{books_url}&limit=1")
    logger.info("Response %s.", response.status_code)
    work_count = response.json()["work_count"]
    book_count = 200
    offset = random.randint(1, work_count - book_count)
    logger.info("Getting random slice of %s books.", book_count)
    response = requests.get(f"{books_url}&limit={book_count}&offset={offset}")
    logger.info("Response %s.", response.status_code)
    books = [
        {"title": item["title"], "author": item["authors"][0]["name"]}
        for item in response.json()["works"]
        if len(item["authors"]) > 0
        and item["authors"][0]["name"].isascii()
        and item["title"].isascii()
    ]
    logger.info("Found %s books.", len(books))

    logger.info("Adding books and authors to the library database.")
    author_count, book_count = storage.add_books(books)
    return author_count, book_count


def do_populate_database(cluster, db_name, secret):
    """
    Creates the demo database and fills it with example data.

    :param cluster: A dict containing cluster details
    :param db_name: The name of the database
    :param secret: A dict containing secret details required to connect to database
    """
    url_get_spider_books = "https://openlibrary.org/subjects/spiders.json?details=false"
    rdsdata_client = boto3.client("rds-data")
    storage = Storage(cluster, secret, db_name, rdsdata_client)
    print(f"Creating tables in database {db_name}.")
    storage.bootstrap_tables()
    print(f"Pulling data from {url_get_spider_books} to populate the demo database.")
    author_count, book_count = fill_db_tables(url_get_spider_books, storage)
    print(f"Added {book_count} books and {author_count} authors.")


def do_deploy_rest(stack_name):
    """
    Calls AWS Chalice and AWS Command Line Interface (AWS CLI) commands to deploy
    the library REST API, including Amazon API Gateway and AWS Lambda resources.

    :param stack_name: The name of the AWS CloudFormation stack to deploy.
    """
    s3 = boto3.resource("s3")
    bucket = s3.create_bucket(Bucket=f"demo-aurora-rest-deploy-{time.time_ns()}")
    print(f"Creating bucket {bucket.name} to hold deployment package.")
    bucket.wait_until_exists()

    commands = [
        "chalice package --merge-template resources.json out",
        f"aws cloudformation package  --template-file out/sam.json "
        f"--s3-bucket {bucket.name} --output-template-file out/template.yml",
        f"aws cloudformation deploy --template-file out/template.yml "
        f"--stack-name {stack_name} --capabilities CAPABILITY_IAM",
    ]

    print(
        "Running AWS Chalice and AWS CloudFormation commands to deploy the "
        "REST API to Amazon API Gateway and AWS Lambda."
    )
    os.chdir("library_api")
    for command in commands:
        print(f"Running '{command}'.")
        os.system(command)
    os.chdir("..")

    bucket.objects.delete()
    bucket.delete()
    print(f"Deleted bucket {bucket.name}.")

    return find_api_url(stack_name)


def do_rest_demo(stack_name):
    """
    Shows how to use the Requests package to call the REST API.

    :param stack_name: The name of the AWS CloudFormation stack used to deploy
                       the REST API. This is used to look up the REST endpoint URL.
    """
    library_url = find_api_url(stack_name)
    books_url = urljoin(library_url, "books/")
    patrons_url = urljoin(library_url, "patrons/")
    lending_url = urljoin(library_url, "lending/")

    print(f"Getting books from {books_url}.")
    response = requests.get(books_url)
    if response.status_code == 408:
        raise TimeoutError(response.json()["Message"])
    else:
        print(f"Response: {response.status_code}")
    books = response.json()
    print(f"Got {len(books['books'])} books. The first five are:")
    pprint(books["books"][:5])

    print(f"Getting patrons from {patrons_url}.")
    response = requests.get(patrons_url)
    print(f"Response: {response.status_code}")
    patrons = response.json()
    print(f"Found {len(patrons['patrons'])} patrons. Let's add one.")
    print("Adding patron 'Dolly Patron' to the library.")
    response = requests.post(
        patrons_url, json={"FirstName": "Dolly", "LastName": "Patron"}
    )
    print(f"Response: {response.status_code}")
    patrons = requests.get(patrons_url).json()
    print(f"Now the library has {len(patrons['patrons'])} patrons. They are:")
    pprint(patrons["patrons"])

    patron = patrons["patrons"][0]
    book = random.choice(books["books"])
    print(f"Lending _{book['Books.Title']}_ to {patron['Patrons.FirstName']}")
    response = requests.put(
        urljoin(lending_url, f"{book['Books.BookID']}/{patron['Patrons.PatronID']}")
    )
    print(f"Response: {response.status_code}")
    lending = requests.get(lending_url).json()
    print("Books currently lent are:")
    pprint(lending["books"])
    print(f"Returning _{book['Books.Title']}_.")
    response = requests.delete(
        urljoin(lending_url, f"{book['Books.BookID']}/{patron['Patrons.PatronID']}")
    )
    print(f"Response: {response.status_code}")


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action", choices=["populate_database", "deploy_rest", "demo_rest"]
    )
    args = parser.parse_args()

    print("-" * 88)
    print("Welcome to the Amazon Relational Database Service (Amazon RDS) demo.")
    print("-" * 88)

    if args.action == "populate_database":
        print("Populating serverless database cluster with data.")
        do_populate_database(config["cluster"], config["db_name"], config["secret"])
        print("Next, run 'py library_demo.py deploy_rest' to deploy the REST API.")
    elif args.action == "deploy_rest":
        print("Deploying the REST API components.")
        api_url = do_deploy_rest(config["cluster"]["cluster_name"])
        print(
            f"Next, send HTTP requests to {api_url} or run "
            f"'py library_demo.py demo_rest' "
            f"to see a demonstration of how to call the REST API by using the "
            f"Requests package."
        )
    elif args.action == "demo_rest":
        print("Demonstrating how to call the REST API by using the Requests package.")
        try:
            do_rest_demo(config["cluster"]["cluster_name"])
        except TimeoutError as err:
            print(err)
        else:
            print(
                "Next, give it a try yourself or run cdk destroy "
                "to delete all demo resources."
            )
    print("-" * 88)


if __name__ == "__main__":
    main()
