import {
  Card,
  CardButton,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/blocks/card";

export default function CardDefault() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Design sync</CardTitle>
        <CardDescription>Weekly review of open work.</CardDescription>
      </CardHeader>
      <CardContent>
        <p>
          The team covered the new navigation pattern, component library updates, and the questions
          that need engineering input before the next release.
        </p>
      </CardContent>
      <CardFooter>
        <CardButton variant="primary">Open notes</CardButton>
        <CardButton variant="secondary">Dismiss</CardButton>
      </CardFooter>
    </Card>
  );
}
