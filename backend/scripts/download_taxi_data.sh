#!/bin/bash
# Download all NYC yellow taxi trip data (Parquet format)
# Available from 2009-01 onwards

DIR="/tmp/kolkhis-data"
mkdir -p "$DIR"

BASE_URL="https://d37ci6vzurychx.cloudfront.net/trip-data"

for year in $(seq 2009 2025); do
  for month in $(seq -w 1 12); do
    FILE="yellow_tripdata_${year}-${month}.parquet"
    URL="${BASE_URL}/${FILE}"
    DEST="${DIR}/${FILE}"

    if [ -f "$DEST" ]; then
      echo "SKIP $FILE (already exists)"
      continue
    fi

    echo "Downloading $FILE..."
    HTTP_CODE=$(curl -s -w "%{http_code}" -L -o "$DEST" "$URL")

    if [ "$HTTP_CODE" != "200" ]; then
      echo "  FAILED (HTTP $HTTP_CODE), removing"
      rm -f "$DEST"
    else
      SIZE=$(ls -lh "$DEST" | awk '{print $5}')
      echo "  OK ($SIZE)"
    fi
  done
done

echo ""
echo "Download complete. Files:"
ls -lh "$DIR"/yellow_tripdata_*.parquet 2>/dev/null | awk '{print $5, $9}'
echo ""
du -sh "$DIR"
