import numpy as np
from gdshelpers.parts.waveguide import Waveguide
from gdshelpers.parts.port import Port


def _arc_length_indefinite_integral(theta, a, b):
    """
    The indefinite integral 

    .. math::
        \f{x} = \int r^2 + \frac{\,dr}{\,d\theta}\,d\theta
        
    which is needed to calculate the arc length of a spiral.
    """
    return np.sqrt( np.square((a + b * theta)) + np.square(b) ) * (a+b*theta) / (2*b) + \
        0.5 * b * np.log(np.sqrt( np.square(a + b*theta) + np.square(b) ) + a + b*theta )

def _arc_length_integral(theta, a, b):
    return _arc_length_indefinite_integral(theta, a, b) - _arc_length_indefinite_integral(0, a, b)

def _spiral_length_angle(theta, a, b, out_angle):
    return (_arc_length_integral(theta, a, b) + # Inward spiral
        np.pi * a + # Two semi-circles in the center
        _arc_length_integral(theta + out_angle, a, b)) # Outward spiral

def _spiral_length_inline(theta, a, b):
    return (_spiral_length_angle(theta, a, b, 0.5*np.pi) +
        0.5 * np.pi * (0.5 * a) + # right bend at the end (min_bend_radius = (0.5 * a))
        ((a + b * theta) - (0.5 * a)))  # from endpoint to startpoint height

def _spiral_length_inline_rel(theta, a, b):
    return (_spiral_length_inline(theta, a, b) -
        (a + b * (theta + 0.5 * np.pi) + (0.5 * a))) # subtract the direct path from input to output

def _spiral_theta(length, wg_width, gap, min_bend_radius, length_function, *args):
    """
    Numerically calculate the theta that is needed for a given spiral variant (length_function) in order
    to have the desired length.

    :param length_function: A function which takes theta, a, b plus arbitrary *args.
    """
    from scipy.optimize import fsolve
    a = 2*min_bend_radius
    b = 2*(wg_width + gap) / (2.*np.pi)
    return fsolve(lambda x: length_function(x, a, b, *args) - length, 20*np.pi)

def _spiral_out_path(t, a, b, max_theta, min_theta=0, theta_offset=0, direction=-1):
    theta = min_theta + t * (max_theta - min_theta)
    r = a + b * theta
    return r * np.array([np.sin(theta + theta_offset), -direction*np.cos(theta + theta_offset)]) + np.array([0, direction*a])[:, None]

def _d_spiral_out_path(t, a, b, max_theta, min_theta=0, theta_offset=0, direction=-1):
    theta = min_theta + t * (max_theta - min_theta)
    r = a + b * theta
    return r * np.array([np.cos(theta + theta_offset), direction*np.sin(theta + theta_offset)])


class Spiral:
    """
    An archimedean spiral, where the length can be numerically calculated.

    Options for the output position are:

        * `inline`
            Output on the same height and direction as input.

        * `inline_rel`
            Output on the same height and direction as input, but the considered length is only the difference of the spiral compared to a straight path.
            This is useful when building MZIs, where the other path is parallel to the spiral.

        * `opposite`
            Output at opposite direction as input

        * `single_inside`
            The spiral will only do a single turn and stop in the inside

        * `single_outside`
            The spiral will only do a single turn, but start in the inside and stop at the outside

        * <phi>, where phi is the angle where the output should be located
    """

    def __init__(self, origin, angle, width, gap, min_bend_radius, theta, output_type='opposite', winding_direction='right', sample_distance=0.50, sample_points=100):
        """
        Create an archimedean spiral following the spiral equation :math:`r = a + b \theta`.
        
        :param origin:
        :param angle:
        :param width:
        :param gap: Gap between the waveguide. Since this is an archimedean spiral, the gap is constant across the whole spiral.
        :param min_bend_radius: The minimum bend radius. This will set the bend of the two semi-circles in the center.
                                It follows that `a = 2 * min_bend_radius`, where `a` as defined in the spiral equation above.
        :param theta: The total angle to turn
        """
        self._origin_port = Port(origin, angle, width)
        self.gap = gap
        self.min_bend_radius = min_bend_radius
        self.total_theta = theta
        self._wg = None
        self.sample_points = sample_points
        self.sample_distance = sample_distance
        self.output_type = output_type
        self.winding_direction = -1 if winding_direction == "left" else 1

        if self.output_type == "inline" or self.output_type == "inline_rel":
            self.out_theta = self.total_theta + 0.5*np.pi
        elif self.output_type == "opposite":
            self.out_theta = self.total_theta
        elif self.output_type == "single_inside" or self.output_type == "single_outside":
            self.out_theta = self.total_theta
        else:
            self.out_theta = self.total_theta + self.output_type

    @classmethod
    def make_at_port(cls, port, **kwargs):
        default_port_param = dict(port.get_parameters())
        default_port_param.update(kwargs)
        del default_port_param['origin']
        del default_port_param['angle']
        del default_port_param['width']

        return cls(port.origin, port.angle, port.width, **default_port_param)

    @classmethod
    def make_at_port_with_length(cls, port, gap, min_bend_radius, target_length, output_type='opposite', **kwargs):
        if output_type == "inline":
            length_fn = [_spiral_length_inline]
        elif output_type == "inline_rel":
            length_fn = [_spiral_length_inline_rel]
        elif output_type == "opposite":
            length_fn = [_spiral_length_angle, 0]
        elif output_type == "single_inside" or output_type == "single_outside":
            length_fn = [_arc_length_integral]
        else:
            length_fn = [_spiral_length_angle, output_type]

        theta = float(_spiral_theta(target_length, port.width, gap, min_bend_radius, *length_fn))
        return cls.make_at_port(port, gap=gap, min_bend_radius=min_bend_radius, theta=theta, output_type=output_type, **kwargs)

    @property
    def width(self):
        return self._origin_port.width

    @property
    def wg(self):
        if not self._wg:
            self._generate()
        return self._wg

    @property
    def length(self):
        return self.wg.length

    @property
    def out_port(self):
        return self.wg.current_port

    def _generate(self):
        self._wg = Waveguide.make_at_port(self._origin_port)
        a = 2*self.min_bend_radius
        b = 2*(self.width + self.gap) / (2.*np.pi)
        outer_r = (a + b*self.total_theta)

        if self.output_type != "single_outside":
            self._wg.add_parameterized_path(lambda x: -_spiral_out_path(1-x, a=a, b=b, max_theta=self.total_theta, theta_offset=-self.total_theta, direction=self.winding_direction) - self.winding_direction*np.array([0, -a + outer_r])[:, None], sample_distance=self.sample_distance, sample_points=self.sample_points,
                                            path_derivative=lambda x: _d_spiral_out_path(1-x, a=a, b=b, max_theta=self.total_theta, theta_offset=-self.total_theta, direction=self.winding_direction),
                                            path_function_supports_numpy=True)

        if self.output_type != "single_inside" and self.output_type != "single_outside":
            self._wg.add_bend(-self.winding_direction*np.pi, self.min_bend_radius)
            self._wg.add_bend(self.winding_direction*np.pi, self.min_bend_radius)

        if self.output_type != "single_inside":
            self._wg.add_parameterized_path(lambda x: _spiral_out_path(x, a=a, b=b, max_theta=self.out_theta, direction=self.winding_direction), sample_distance=self.sample_distance, sample_points=self.sample_points,
                                            path_derivative=lambda x: _d_spiral_out_path(x, a=a, b=b, max_theta=self.out_theta, direction=self.winding_direction),
                                            path_function_supports_numpy=True)

        if self.output_type == "inline" or self.output_type == "inline_rel":
            self._wg.add_straight_segment((outer_r - self.min_bend_radius))
            self._wg.add_bend(-0.5*self.winding_direction*np.pi, self.min_bend_radius)

    def get_shapely_object(self):
        return self.wg.get_shapely_object()


if __name__ == '__main__':
    from gdshelpers.geometry.chip import Cell
    from gdshelpers.parts.text import Text
    cell = Cell('Spiral')

    def demo_spiral(origin, output_type, target_length, gap, port_y_offset=0):
        wg = Waveguide(origin + np.array([0, port_y_offset]), 0, 1)
        wg.add_straight_segment(30)
        spiral = Spiral.make_at_port_with_length(wg.current_port, gap=gap, min_bend_radius=35., target_length=target_length, output_type=output_type, sample_distance=1)
        text = Text(np.array([150, -130]) + origin, 20, "output: {}\n\nlength: {} um\nreal_length: {:.4f}um".format(output_type, target_length, spiral.length))
        spiral.wg.add_straight_segment(30)
        cell.add_to_layer(1, wg, spiral)
        cell.add_to_layer(2, text)

    # Create normal demo spirals
    for i,output_type in enumerate(['opposite', 'inline', 'inline_rel', -0.5*np.pi, 0.25*np.pi, np.pi]):
        demo_spiral(((i//4)*700, (i%4)*250), output_type, 2000, gap=5.)

    # Create spirals with single turn
    demo_spiral((1*700, 2*250), 'single_inside', 2000, gap=1.5)
    demo_spiral((1*700, 3*250), 'single_outside', 2000, gap=1.5, port_y_offset=-150)

    cell.show()
    #cell.save("spiral_test")
