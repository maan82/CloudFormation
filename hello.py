import boto

conn = boto.connect_s3()

for b in conn.get_all_buckets():
    print b.name