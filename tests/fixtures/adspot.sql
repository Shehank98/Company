-- Subset of adspot_media/server/migrations (real column names).
DROP TABLE IF EXISTS bookings, design_requests CASCADE;
CREATE TABLE bookings (
  id SERIAL PRIMARY KEY, booking_id VARCHAR UNIQUE, status VARCHAR DEFAULT 'pending',
  payment_status VARCHAR DEFAULT 'pending', receipt_status VARCHAR DEFAULT 'none',
  artwork_status VARCHAR DEFAULT 'pending', total_amount NUMERIC, items JSONB NOT NULL DEFAULT '[]',
  customer_email VARCHAR, created_at TIMESTAMPTZ DEFAULT now(), updated_at TIMESTAMPTZ DEFAULT now(),
  sent_to_publication_at TIMESTAMPTZ, published_at TIMESTAMPTZ);
CREATE TABLE design_requests (id SERIAL PRIMARY KEY, status VARCHAR DEFAULT 'pending');
INSERT INTO bookings (booking_id,status,payment_status,receipt_status,total_amount,items,customer_email,created_at) VALUES
 ('BOOK-20260101-001','pending','pending','none',5000,'[{"pubDate":"2099-01-01"}]','a@example.com', now() - interval '5 days'),
 ('BOOK-20260101-002','paid','completed','approved',12000,
   jsonb_build_array(jsonb_build_object('pubDate', to_char(current_date + 2,'YYYY-MM-DD'))),'b@example.com', now()),
 ('BOOK-20260101-003','pending','pending','submitted',7000,'[{"pubDate":"not-a-date"}]','c@example.com', now());
INSERT INTO design_requests DEFAULT VALUES;
