-- Subset of adspot_media/server/migrations (real column names).
DROP TABLE IF EXISTS bookings, design_requests, newspapers, publication_groups CASCADE;
CREATE TABLE publication_groups (id VARCHAR PRIMARY KEY, name VARCHAR NOT NULL, contact_email VARCHAR DEFAULT '', active BOOLEAN DEFAULT true);
CREATE TABLE newspapers (id VARCHAR PRIMARY KEY, group_id VARCHAR, name VARCHAR NOT NULL);
INSERT INTO publication_groups VALUES ('lake-house','Lake House',''), ('wijeya','Wijeya','');
INSERT INTO newspapers VALUES ('dn','lake-house','Daily News'), ('island','upali','The Island'), ('lankadeepa','wijeya','Lankadeepa');
CREATE TABLE bookings (
  id SERIAL PRIMARY KEY, booking_id VARCHAR UNIQUE, quotation_number VARCHAR, customer_name VARCHAR, status VARCHAR DEFAULT 'pending',
  payment_status VARCHAR DEFAULT 'pending', payment_reference VARCHAR, receipt_status VARCHAR DEFAULT 'none',
  artwork_status VARCHAR DEFAULT 'pending', total_amount NUMERIC, items JSONB NOT NULL DEFAULT '[]',
  customer_email VARCHAR, created_at TIMESTAMPTZ DEFAULT now(), updated_at TIMESTAMPTZ DEFAULT now(),
  sent_to_publication_at TIMESTAMPTZ, published_at TIMESTAMPTZ);
CREATE TABLE design_requests (id SERIAL PRIMARY KEY, status VARCHAR DEFAULT 'pending');
INSERT INTO bookings (booking_id,quotation_number,customer_name,status,payment_status,receipt_status,total_amount,items,customer_email,created_at) VALUES
 ('BOOK-20260101-001','ADM-2026-1001','Ann Perera','pending','pending','none',5000,'[{"newspaperId":"dn","newspaperName":"Daily News","groupName":"Lake House","adType":"classified","pubDate":"2099-01-01","price":5000,"details":{"adTotal":4500,"text":"Room for rent in Colombo 5","wordCount":6},"adFileUrl":null}]','a@example.com', now() - interval '5 days'),
 ('BOOK-20260101-002','ADM-2026-1002','Bimal Silva','paid','completed','approved',12000,
   jsonb_build_array(jsonb_build_object('pubDate', to_char(current_date + 2,'YYYY-MM-DD'))),'b@example.com', now()),
 ('BOOK-20260101-003','ADM-2026-1003','Chamari Fernando','pending','pending','submitted',7000,'[{"pubDate":"not-a-date"}]','c@example.com', now());
INSERT INTO design_requests DEFAULT VALUES;
