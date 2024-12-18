import pandas as pd
import sys
import os
import json


def generate_materialized_name(folder_name, csv_name, state_lookup, national_lookup):
    type_char = folder_name.split("_")[1][0].lower()
    folder_code = folder_name.split("_")[1][1:].upper()
    human_readable_name = "individual_people" if type_char == "p" else "housing_units"

    if folder_code == "US":
        csv_code = csv_name.split("_")[1][1:].upper()
        name = national_lookup.get(csv_code, "Unknown national code")
    elif len(folder_code) == 2:
        name = state_lookup.get(folder_code, "Unknown state code")
    else:
        raise ValueError(f"Invalid code: {folder_code}")

    return f"{human_readable_name}_{name.replace(' ', '_')}".lower()


def clean_enum_value(value):
    value = value.replace("'", "")
    value = value.replace("N/A", "Not applicable")
    value = value.replace("/", " or ")
    value = value.replace("(", "- ")
    value = value.replace(")", "")
    return value


if len(sys.argv) < 3:
    print("Usage: python script.py <parquet_database_path> <PUMS_data_dictionary_path>")
    sys.exit(1)

parquet_database_path, data_dictionary_path = sys.argv[1:3]

year = data_dictionary_path.split("/")[-1].split(".")[0].split("_")[-1]
print(f"Year: {year}")
with open(data_dictionary_path, "r") as json_file:
    data_dict = json.load(json_file)

state_lookup = {
    code: name
    for name, code in [x.split("/") for x in data_dict["ST"]["Values"].values()]
}
national_lookup = {
    "USA": "United States first tranche",
    "USB": "United States second tranche",
}

df_csv_paths = pd.read_parquet(parquet_database_path)
models_dir = (
    f"models/public_use_microdata_sample/generated/{year}/enum_types_mapped_renamed"
)
os.makedirs(models_dir, exist_ok=True)


def should_include_key(description):
    exclude_criteria = [
        "weight",
        "identifier",
        "number",
        "age",
        "income",
        "time",
        "hours",
        "weeks",
        "puma",
        "total",
        "fee",
        "cost",
        "amount",
        "rent",
        "value",
        "taxes",
    ]
    # Check if any of the exclude criteria are in the value or if "age" is in the description.
    if any(
        criterion in description.lower() and "flag" not in description.lower()
        for criterion in exclude_criteria
    ):
        return False
    return True


for csv_path in df_csv_paths["csv_path"]:
    folder_name = os.path.basename(os.path.dirname(csv_path))
    csv_name = os.path.basename(csv_path).split(".")[0]
    materialized_name = generate_materialized_name(
        folder_name, csv_name, state_lookup, national_lookup
    )

    df_headers = pd.read_csv(csv_path, nrows=0)
    column_types = {column: "VARCHAR" for column in df_headers.columns}
    columns = ", ".join([f"'{col}': '{typ}'" for col, typ in column_types.items()])
    sql_select_parts = ["SELECT"]
    enum_creation_statements = []
    table_creation_statement = f"CREATE TABLE {materialized_name} ("
    column_definitions = []
    newline = "\n"

    for header, details in data_dict.items():

        if "Values" in details:
            if header in df_headers.columns:
                enum_values = [
                    f"'{key.strip()}'" for key, value in details["Values"].items()
                ]
                col_info = data_dict.get(header, {"Description": header})
                description = col_info["Description"]

                if should_include_key(details["Description"]) and len(enum_values) > 0:
                    enum_name = f"{header}_enum"
                    value_mapping = "\n\t\t".join(
                        [
                            f"WHEN '{clean_enum_value(code)}' THEN '{clean_enum_value(label)}'"
                            for code, label in data_dict[header]["Values"].items()
                        ]
                    )
                    enum_labels = [
                        f"'{clean_enum_value(label)}'"
                        for code, label in data_dict[header]["Values"].items()
                    ]
                    mapped_column = f"""CASE {header}\n\t\t{value_mapping}\n\tEND::ENUM ({','.join(enum_labels)}) AS "{description}","""
                    column_definitions.append(mapped_column)
                else:
                    column_definitions.append(
                        f'    {header}::VARCHAR AS "{description}",'
                    )
            else:
                # print(f"Column {header} not found in {csv_name}.csv")
                pass

    sql_select_parts[-1] = sql_select_parts[-1].rstrip(",")
    sql_select_statement = "\n".join(sql_select_parts)
    newline = "\n"
    newline_with_comma = ",\n"
    username = os.environ.get("USER")
    path_without_user = "~/" + csv_path.split(username + '/')[1]
    # Combine ENUM creation, table creation, and COPY command in SQL content
    sql_content = f"""-- SQL transformation for {csv_name} generated by models/public_use_microdata_sample/scripts/{os.path.basename(__file__)}
{{{{ config(materialized='external', location=var('output_path') + '/acs_pums_{materialized_name}_{year}.parquet') }}}}

SELECT
{newline.join(column_definitions)}
FROM read_csv('{path_without_user}', 
              parallel=False,
              all_varchar=True,
              auto_detect=True)
"""

    sql_file_path = os.path.join(
        models_dir, f"{materialized_name}_enum_mapped_renamed_{year}.sql"
    )
    with open(sql_file_path, "w") as sql_file:
        sql_file.write(sql_content)
