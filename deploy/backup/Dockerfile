# Atsarginių kopijų konteineris: pg_dump (ta pati Postgres versija) + sqlite3 (nuosekli uno.db kopija).
FROM postgres:16.6-alpine
RUN apk add --no-cache sqlite tar gzip
COPY backup.sh /usr/local/bin/backup.sh
RUN chmod 0755 /usr/local/bin/backup.sh
ENTRYPOINT ["/usr/local/bin/backup.sh"]
CMD ["loop"]
