import { IconDots } from "@tabler/icons-react";

import { IconButton } from "@/blocks/action-bar";
import {
  Menu,
  MenuContent,
  MenuItem,
  MenuLabel,
  MenuSeparator,
  MenuTrigger,
} from "@/blocks/menu";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/blocks/table";

const PRODUCTS = [
  { id: "PRD001", name: "Wireless keyboard", stock: 128, price: "$89.00" },
  { id: "PRD002", name: "Studio monitor", stock: 24, price: "$349.00" },
  { id: "PRD003", name: "Desk lamp", stock: 0, price: "$62.00" },
  { id: "PRD004", name: "Laptop stand", stock: 76, price: "$45.00" },
];

export function TableActions() {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead width={88}>Product</TableHead>
          <TableHead>Name</TableHead>
          <TableHead align="right" width={72}>
            Stock
          </TableHead>
          <TableHead align="right" width={88}>
            Price
          </TableHead>
          <TableHead width={16} />
        </TableRow>
      </TableHeader>
      <TableBody>
        {PRODUCTS.map((product) => (
          <TableRow key={product.id}>
            <TableCell>{product.id}</TableCell>
            <TableCell>{product.name}</TableCell>
            <TableCell align="right" muted>
              {product.stock}
            </TableCell>
            <TableCell align="right">{product.price}</TableCell>
            <TableCell>
              <Menu>
                <MenuTrigger asChild>
                  <IconButton label={`Actions for ${product.name}`}>
                    <IconDots size={16} stroke={1.5} />
                  </IconButton>
                </MenuTrigger>
                <MenuContent>
                  <MenuLabel>Actions</MenuLabel>
                  <MenuItem>Copy ID</MenuItem>
                  <MenuSeparator />
                  <MenuItem>View details</MenuItem>
                  <MenuItem destructive>Delete</MenuItem>
                </MenuContent>
              </Menu>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
