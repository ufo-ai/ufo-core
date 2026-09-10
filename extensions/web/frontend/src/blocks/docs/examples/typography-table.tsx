import { Prose } from "@/blocks/typography";

export default function TypographyTable() {
  return (
    <Prose>
      <table>
        <thead>
          <tr>
            <th>Object</th>
            <th>Verb</th>
            <th>Who can call it</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>Connector</td>
            <td>connect</td>
            <td>Any member</td>
          </tr>
          <tr>
            <td>Issue</td>
            <td>create</td>
            <td>The workspace agent</td>
          </tr>
          <tr>
            <td>Grant</td>
            <td>revoke</td>
            <td>An owner</td>
          </tr>
        </tbody>
      </table>
    </Prose>
  );
}
