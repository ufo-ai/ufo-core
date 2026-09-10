export type Payment = {
  id: string;
  status: "Pending" | "Processing" | "Success" | "Failed";
  email: string;
  amount: number;
};

export const AMOUNT = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });

export const PAYMENTS: Payment[] = [
  { id: "m5gr84i9", status: "Success", email: "ken99@yahoo.com", amount: 316 },
  { id: "3u1reuv4", status: "Success", email: "abe45@gmail.com", amount: 242 },
  { id: "derv1ws0", status: "Processing", email: "monserrat44@gmail.com", amount: 837 },
  { id: "5kma53ae", status: "Success", email: "silas22@gmail.com", amount: 874 },
  { id: "bhqecj4p", status: "Failed", email: "carmella@hotmail.com", amount: 721 },
  { id: "p2xv91qc", status: "Pending", email: "rowan.d@fastmail.com", amount: 158 },
  { id: "t8yn30bd", status: "Processing", email: "imani.k@outlook.com", amount: 493 },
  { id: "w4kz62he", status: "Success", email: "tobias@work.com", amount: 205 },
  { id: "q9jd17mf", status: "Failed", email: "noor.h@proton.me", amount: 612 },
  { id: "z3fb85rg", status: "Pending", email: "elena.v@gmail.com", amount: 349 },
  { id: "c6ta40nh", status: "Success", email: "kwame.a@yahoo.com", amount: 128 },
  { id: "v1pl73sj", status: "Processing", email: "yuki.s@icloud.com", amount: 964 },
  { id: "n7wc28dk", status: "Success", email: "hugo.m@fastmail.com", amount: 431 },
  { id: "x2rq56fl", status: "Pending", email: "aisha.b@outlook.com", amount: 277 },
  { id: "b8hs91gm", status: "Failed", email: "dario@work.com", amount: 803 },
  { id: "j4vn12kn", status: "Success", email: "leila.n@gmail.com", amount: 546 },
  { id: "s0dm67pq", status: "Processing", email: "matteo@proton.me", amount: 190 },
  { id: "y5cz34tr", status: "Success", email: "priya.r@yahoo.com", amount: 688 },
  { id: "k9xb80vs", status: "Pending", email: "olamide@icloud.com", amount: 355 },
  { id: "f3nw45wt", status: "Failed", email: "sven.l@outlook.com", amount: 912 },
  { id: "d7qj19xu", status: "Success", email: "mira.c@work.com", amount: 264 },
  { id: "g1tk73yv", status: "Processing", email: "zane.p@gmail.com", amount: 578 },
  { id: "h6vs28zw", status: "Success", email: "anouk@fastmail.com", amount: 143 },
  { id: "l2bd90ax", status: "Pending", email: "rafael.o@proton.me", amount: 736 },
];
